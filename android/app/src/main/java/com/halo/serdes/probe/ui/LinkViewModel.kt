package com.halo.serdes.probe.ui

import android.app.Application
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.viewModelScope
import com.halo.serdes.probe.HaloPython
import com.halo.serdes.probe.api.ApiResult
import com.halo.serdes.probe.api.HaloApi
import com.halo.serdes.probe.api.stringOrNull
import com.halo.serdes.probe.run.Quality
import com.halo.serdes.probe.run.TimeRunController
import com.halo.serdes.probe.touchstone.TouchstoneImport
import com.halo.serdes.probe.touchstone.TouchstoneInfo
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import android.content.Context
import android.net.Uri
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import kotlinx.coroutines.launch
import org.json.JSONObject

/** What one press of Run produced. */
data class StatSummary(
    val handle: String,
    val ber: Double,
    val ser: Double,
    val bestPhi: Int,
    val elapsedS: Double,
    /** Phase axis in UI and the BER at each phase — the bathtub, plot-ready. */
    val bathtubX: List<Double> = emptyList(),
    val bathtubY: List<Double> = emptyList(),
)

/**
 * The statistical eye, already reduced and log10-scaled by the facade.
 *
 * `floor` marks cells where no probability was resolved at all, which is not
 * the same as a very small one — the renderer shows them as absence.
 */
data class EyeMap(
    val z: List<List<Double>>,
    val zmin: Double,
    val zmax: Double,
    val floor: Double,
)

/**
 * What a finished time-domain run measured.
 *
 * [berIsUpperBound] is the field that keeps this honest. A comfortable link at
 * the fast tier makes zero errors, and the engine then reports `ber == 0.0` —
 * which reads as "perfect" but means "below what this many symbols can see".
 * [berBound] is that visibility limit, 1/n_checked.
 */
data class TimeSummary(
    val handle: String,
    val ber: Double,
    val ser: Double,
    val nErrors: Int,
    val nChecked: Int,
    val nSymbols: Int,
    val nRequested: Int,
    val slicerSnrDb: Double,
    val elapsedS: Double,
    val berIsUpperBound: Boolean,
) {
    val berBound: Double get() = if (nChecked > 0) 1.0 / nChecked else Double.NaN
}

/** The mixed-signal envelope banner: `ok` / `warn` / `crit` plus a message. */
data class Envelope(val level: String, val message: String)

data class LinkUiState(
    val sections: List<FormSection> = emptyList(),
    val presets: List<String> = emptyList(),
    val selected: String = "",
    val configsDir: String = "",
    /** Flat `{path: value}` — Boolean for bool fields, String for the rest. */
    val values: Map<String, Any> = emptyMap(),
    val derived: Map<String, String> = emptyMap(),
    val fieldErrors: Map<String, String> = emptyMap(),
    val envelope: Envelope? = null,
    /**
     * Set when this config's channel data is not reachable — an Android build
     * ships the YAML presets but not the 4.4 MB of `.s4p` files. The config is
     * valid; the data simply is not here, so say so up front instead of
     * letting Run fail.
     */
    val channelIssue: String? = null,
    val stat: StatSummary? = null,
    val time: TimeSummary? = null,
    /** A picked Touchstone file, inspected but not yet adopted. */
    val touchstone: TouchstoneInfo? = null,
    val touchstoneBusy: Boolean = false,
    val touchstoneError: String? = null,
    /** Studies as the facade advertises them — never hardcoded on this side. */
    val studies: List<StudyMeta> = emptyList(),
    val studyResults: Map<String, StudyResult> = emptyMap(),
    /** Name of the study currently running, if any. */
    val studyRunning: String? = null,
    val eye: EyeMap? = null,
    val eyeLoading: Boolean = false,
    val warnings: List<String> = emptyList(),
    val busy: Boolean = false,
    val validating: Boolean = false,
    val error: ApiResult.Err? = null,
) {
    val ready: Boolean
        get() = values.isNotEmpty() && !busy && channelIssue == null && fieldErrors.isEmpty()
}

/**
 * Drives the screen from the JSON facade. It holds no simulation logic of its
 * own — every number on screen comes from one of three calls: `schema`,
 * `derive`, `run_stat`.
 *
 * The previous run's handle is released before a new one is stored, so the
 * process-side registry keeps at most one result's numpy arrays alive. Nothing
 * else on this side is allowed to hold a handle.
 */
class LinkViewModel(app: Application) : AndroidViewModel(app) {

    private val _state = MutableStateFlow(LinkUiState())
    val state: StateFlow<LinkUiState> = _state.asStateFlow()

    private val ctx get() = getApplication<Application>()

    /** Cancelled and replaced on each keystroke, so typing runs one derive. */
    private var deriveJob: Job? = null

    init {
        loadSchema()
        watchTimeRuns()
    }

    /**
     * Turn a finished job into a summary.
     *
     * The controller only knows the job reached `done` and hands over a
     * handle; reading what it measured is a separate call, made once here
     * rather than by the screen, so a rotation does not re-fetch it.
     */
    private fun watchTimeRuns() = viewModelScope.launch {
        var seen: String? = null
        TimeRunController.state.collect { r ->
            val handle = r.handle
            if (r.state != "done" || handle == null || handle == seen) return@collect
            seen = handle
            val res = HaloApi.call(ctx, "result", JSONObject().put("handle", handle))
            if (res !is ApiResult.Ok) return@collect
            val sim = res.data.optJSONObject("sim") ?: return@collect
            val previous = _state.value.time?.handle
            _state.update {
                it.copy(time = TimeSummary(
                    handle = handle,
                    ber = sim.optDouble("ber"),
                    ser = sim.optDouble("ser"),
                    nErrors = sim.optInt("n_errors"),
                    nChecked = sim.optInt("n_checked"),
                    nSymbols = sim.optInt("n_symbols"),
                    nRequested = sim.optInt("n_requested"),
                    slicerSnrDb = sim.optDouble("slicer_snr_db"),
                    elapsedS = res.data.optDouble("elapsed_s"),
                    berIsUpperBound = sim.optBoolean("ber_is_upper_bound"),
                ))
            }
            if (previous != null && previous != handle) HaloApi.release(ctx, previous)
        }
    }

    /**
     * Start a time-domain run at the chosen tier.
     *
     * The values go to the controller, not to a coroutine here: the run must
     * outlive this ViewModel, which a rotation destroys.
     */
    fun startTimeRun(context: Context, quality: Quality) {
        val values = _state.value.values
        if (values.isEmpty()) return
        TimeRunController.start(context, values.toJson(), quality)
    }

    fun clearTimeRun(context: Context) {
        TimeRunController.clear(context)
        _state.update { it.copy(time = null) }
    }

    private fun loadSchema() = viewModelScope.launch {
        _state.update { it.copy(busy = true, error = null) }
        when (val r = HaloApi.schema(ctx)) {
            is ApiResult.Err -> _state.update { it.copy(busy = false, error = r) }
            is ApiResult.Ok -> {
                val names = r.data.optJSONArray("presets")
                    ?.let { a -> List(a.length()) { a.optString(it) } }
                    ?: emptyList()
                _state.update {
                    it.copy(
                        busy = false,
                        sections = parseSections(r.data),
                        presets = names,
                        studies = parseStudies(r.data),
                        configsDir = r.data.optString("configs_dir"),
                    )
                }
                firstRunnable(names)?.let { select(it) }
            }
        }
    }

    /**
     * The preset to open on: the first that can actually run here.
     *
     * "Library defaults" is skipped because it is synthesised — its channel is
     * a touchstone with no file, so it is a starting point for editing rather
     * than something to run. The rest are skipped when their `.s4p` is absent,
     * which on Android is most of the touchstone ones. Opening on a preset
     * whose Run button is disabled would read as a broken app.
     *
     * Cheap to probe: `derive` builds a config and stats a path, no engine.
     */
    private suspend fun firstRunnable(names: List<String>): String? {
        val candidates = names.filter { it != LIBRARY_DEFAULTS }
        for (name in candidates) {
            val p = HaloApi.preset(ctx, name) as? ApiResult.Ok ?: continue
            val values = p.data.optJSONObject("values") ?: continue
            val d = HaloApi.derive(ctx, values) as? ApiResult.Ok ?: continue
            val ch = d.data.optJSONObject("channel")
            if (ch == null || ch.optBoolean("ok", true)) return name
        }
        // Nothing runnable: still select something, so the screen explains why
        // rather than showing an empty picker.
        return candidates.firstOrNull()
    }

    fun select(name: String) = viewModelScope.launch {
        deriveJob?.cancel()
        _state.update {
            // Study results belong to the config that produced them; keeping
            // them across a preset change would show one link's curves under
            // another link's name.
            it.copy(selected = name, busy = true, error = null, stat = null,
                    channelIssue = null, fieldErrors = emptyMap(),
                    studyResults = emptyMap())
        }
        when (val p = HaloApi.preset(ctx, name)) {
            is ApiResult.Err -> _state.update { it.copy(busy = false, error = p) }
            is ApiResult.Ok -> {
                val fields = _state.value.sections.flatMap { it.fields }
                val values = (p.data.optJSONObject("values") ?: JSONObject())
                    .toFormValues(fields)
                _state.update { it.copy(values = values) }
                derive(values)
            }
        }
    }

    /**
     * Edit one field.
     *
     * Validation is debounced rather than run per keystroke: a half-typed
     * number ("1e", "-") is not an error the user has made yet, and flashing
     * red between characters trains people to ignore the marker.
     */
    fun setField(path: String, value: Any) {
        _state.update {
            it.copy(values = it.values + (path to value), validating = true)
        }
        deriveJob?.cancel()
        deriveJob = viewModelScope.launch {
            delay(DERIVE_DEBOUNCE_MS)
            derive(_state.value.values)
        }
    }

    private suspend fun derive(values: Map<String, Any>) = derive(values.toJson())

    private suspend fun derive(values: JSONObject) {
        when (val d = HaloApi.derive(ctx, values)) {
            is ApiResult.Err ->
                _state.update { it.copy(busy = false, validating = false, error = d) }
            is ApiResult.Ok -> {
                val data = d.data
                val errs = data.optJSONObject("field_errors").toStringMap()
                val env = data.optJSONObject("envelope")
                val ch = data.optJSONObject("channel")
                _state.update {
                    it.copy(
                        busy = false,
                        validating = false,
                        fieldErrors = errs,
                        // An invalid config carries no derived quantities and no
                        // channel verdict; keep the last good ones rather than
                        // blanking the panel mid-edit.
                        derived = data.optJSONObject("derived")
                            ?.toStringMap() ?: it.derived,
                        channelIssue = ch
                            ?.takeIf { c -> !c.optBoolean("ok", true) }
                            ?.let { c ->
                                c.stringOrNull("message")
                                    ?: "channel data unavailable"
                            },
                        envelope = env
                            ?.takeIf { e -> e.optString("message").isNotBlank() }
                            ?.let { e ->
                                Envelope(e.optString("level"), e.optString("message"))
                            },
                    )
                }
            }
        }
    }

    fun run() = viewModelScope.launch {
        val values = _state.value.values
        if (values.isEmpty()) return@launch
        val previous = _state.value.stat?.handle
        _state.update { it.copy(busy = true, error = null, eye = null) }

        when (val r = HaloApi.runStat(ctx, values.toJson())) {
            is ApiResult.Err -> _state.update { it.copy(busy = false, error = r) }
            is ApiResult.Ok -> {
                val d = r.data
                _state.update {
                    it.copy(
                        busy = false,
                        warnings = r.warnings,
                        stat = StatSummary(
                            handle = d.optString("handle"),
                            ber = d.optDouble("ber"),
                            ser = d.optDouble("ser"),
                            bestPhi = d.optInt("best_phi"),
                            elapsedS = d.optDouble("elapsed_s"),
                            bathtubX = d.optJSONObject("bathtub")
                                ?.optJSONArray("x").toDoubleList(),
                            bathtubY = d.optJSONObject("bathtub")
                                ?.optJSONArray("y").toDoubleList(),
                        ),
                    )
                }
                previous?.let { HaloApi.release(ctx, it) }
            }
        }
    }

    /**
     * Fetch the statistical eye for the current result.
     *
     * On demand rather than with every run: it is the one payload big enough to
     * be worth not sending — a reduced map is still ~4k numbers, and most
     * presses of Run are to read a BER, not to look at the eye.
     */
    fun loadEye() = viewModelScope.launch {
        val handle = _state.value.stat?.handle ?: return@launch
        if (_state.value.eye != null || _state.value.eyeLoading) return@launch
        _state.update { it.copy(eyeLoading = true) }

        val payload = JSONObject().put("handle", handle).put("key", "stat_eye")
        when (val r = HaloApi.call(ctx, "series", payload)) {
            is ApiResult.Err ->
                _state.update { it.copy(eyeLoading = false, error = r) }
            is ApiResult.Ok -> {
                val rows = r.data.optJSONArray("z")
                val z = if (rows == null) emptyList()
                        else (0 until rows.length()).map {
                            rows.optJSONArray(it).toDoubleList()
                        }
                _state.update {
                    it.copy(
                        eyeLoading = false,
                        eye = EyeMap(
                            z = z,
                            zmin = r.data.optDouble("zmin", -18.0),
                            zmax = r.data.optDouble("zmax", 0.0),
                            floor = r.data.optDouble("floor", -18.0),
                        ),
                    )
                }
            }
        }
    }

    /**
     * Inspect a picked Touchstone file. Nothing is adopted yet.
     *
     * Two steps, on two dispatchers: the copy is content-provider I/O and
     * belongs on IO, while the read goes through the interpreter thread like
     * every other Python call.
     */
    fun importTouchstone(uri: Uri) = viewModelScope.launch {
        _state.update {
            it.copy(touchstoneBusy = true, touchstoneError = null, touchstone = null)
        }
        val file = try {
            withContext(Dispatchers.IO) { TouchstoneImport.copyIn(ctx, uri) }
        } catch (t: Throwable) {
            _state.update {
                it.copy(touchstoneBusy = false,
                        touchstoneError = t.message ?: t.toString())
            }
            return@launch
        }
        // The current values go along so the report can compare the file's
        // span against *this* config's Nyquist — the number that decides
        // whether the file answers the question being asked.
        val payload = JSONObject()
            .put("path", file.absolutePath)
            .put("values", _state.value.values.toJson())
        when (val r = HaloApi.call(ctx, "import_touchstone", payload)) {
            is ApiResult.Err -> {
                file.delete()
                _state.update {
                    it.copy(touchstoneBusy = false, touchstoneError = r.message)
                }
            }
            is ApiResult.Ok -> _state.update {
                it.copy(touchstoneBusy = false, touchstone = TouchstoneInfo(
                    path = r.data.optString("file"),
                    name = r.data.optString("name"),
                    fileFMaxGhz = r.data.optDouble("file_f_max_ghz"),
                    nyquistGhz = r.data.optDouble("nyquist_ghz"),
                    ilDbAtNyquist = r.data.optDouble("il_db_at_nyquist"),
                    nFreq = r.data.optInt("n_freq"),
                    extrapolated = r.data.optBoolean("extrapolated"),
                ))
            }
        }
    }

    /**
     * Point the config at the imported file.
     *
     * Written through [setField] rather than into `values` directly, so the
     * same debounce-and-derive path runs — an adopted channel is validated by
     * exactly the code that validates a typed one.
     */
    fun adoptTouchstone(info: TouchstoneInfo) {
        setField("channel.kind", "touchstone")
        setField("channel.file", info.path)
        _state.update { it.copy(touchstone = null) }
    }

    fun discardTouchstone() {
        _state.update { it.copy(touchstone = null, touchstoneError = null) }
    }

    /**
     * Run one sweep against the current parameters.
     *
     * Serialised by [LinkUiState.studyRunning] rather than queued: they all go
     * through the one interpreter thread anyway, and letting several be
     * started would only hide which one the spinner belonged to. The slowest
     * (jtol, fixedpoint) are minutes, so this matters.
     */
    fun runStudy(name: String) = viewModelScope.launch {
        if (_state.value.studyRunning != null) return@launch
        _state.update { it.copy(studyRunning = name, error = null) }
        val payload = JSONObject()
            .put("name", name)
            .put("values", _state.value.values.toJson())
        // A study that needs a time-domain record gets the handle of the run
        // already sitting on the Link screen, rather than making the facade
        // start another one inside this call — where it would block with no
        // progress and no way to stop it.
        val meta = _state.value.studies.firstOrNull { it.name == name }
        if (meta?.needsTime == true) {
            _state.value.time?.handle?.let { payload.put("handle", it) }
        }
        when (val r = HaloApi.call(ctx, "study", payload)) {
            is ApiResult.Err ->
                _state.update { it.copy(studyRunning = null, error = r) }
            is ApiResult.Ok -> {
                val previous = _state.value.studyResults[name]?.handle
                val result = StudyResult(
                    name = name,
                    // The spec that came back with the data, not the one from
                    // schema: they are the same today, and reading it here
                    // means they cannot disagree tomorrow.
                    plots = parsePlots(r.data.optJSONArray("plots")),
                    data = parseStudyData(r.data.optJSONObject("data")),
                    note = r.data.stringOrNull("note"),
                    handle = r.data.stringOrNull("handle"),
                )
                _state.update {
                    it.copy(studyRunning = null,
                            studyResults = it.studyResults + (name to result))
                }
                if (previous != null && previous != result.handle) {
                    HaloApi.release(ctx, previous)
                }
            }
        }
    }

    override fun onCleared() {
        // Only the statistical handle. The time-domain one belongs to
        // TimeRunController, which outlives this ViewModel on purpose —
        // releasing it here would delete a finished run's result on rotation.
        val handle = _state.value.stat?.handle
        if (handle != null) {
            // viewModelScope is already cancelled here, so a coroutine would
            // never run — post straight to the interpreter thread instead.
            HaloPython.post {
                runCatching {
                    HaloPython.callOnThisThread(
                        "release", JSONObject().put("handle", handle).toString()
                    )
                }
            }
        }
        super.onCleared()
    }

    private companion object {
        const val LIBRARY_DEFAULTS = "Library defaults"
        const val DERIVE_DEBOUNCE_MS = 300L
    }
}

private fun org.json.JSONArray?.toDoubleList(): List<Double> =
    if (this == null) emptyList() else List(length()) { optDouble(it) }

private fun JSONObject?.toStringMap(): Map<String, String> {
    if (this == null) return emptyMap()
    val out = LinkedHashMap<String, String>(length())
    keys().forEach { k -> out[k] = optString(k) }
    return out
}
