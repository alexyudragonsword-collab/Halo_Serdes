package com.halo.serdes.probe.ui

import android.app.Application
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.viewModelScope
import com.halo.serdes.probe.HaloPython
import com.halo.serdes.probe.api.ApiResult
import com.halo.serdes.probe.api.HaloApi
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import org.json.JSONObject

/** What one press of Run produced. */
data class StatSummary(
    val handle: String,
    val ber: Double,
    val ser: Double,
    val bestPhi: Int,
    val bathtubPoints: Int,
    val elapsedS: Double,
)

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
            it.copy(selected = name, busy = true, error = null, stat = null,
                    channelIssue = null, fieldErrors = emptyMap())
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
                            ?.optString("message")
                            ?.ifBlank { "channel data unavailable" },
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
        _state.update { it.copy(busy = true, error = null) }

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
                            bathtubPoints = d.optJSONObject("bathtub")
                                ?.optJSONArray("x")?.length() ?: 0,
                            elapsedS = d.optDouble("elapsed_s"),
                        ),
                    )
                }
                previous?.let { HaloApi.release(ctx, it) }
            }
        }
    }

    override fun onCleared() {
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

private fun JSONObject?.toStringMap(): Map<String, String> {
    if (this == null) return emptyMap()
    val out = LinkedHashMap<String, String>(length())
    keys().forEach { k -> out[k] = optString(k) }
    return out
}
