package com.halo.serdes.probe.ui

import android.app.Application
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.viewModelScope
import com.halo.serdes.probe.HaloPython
import com.halo.serdes.probe.api.ApiResult
import com.halo.serdes.probe.api.HaloApi
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
    val presets: List<String> = emptyList(),
    val selected: String = "",
    val configsDir: String = "",
    /** Flat `{path: value}` for the selected preset, passed back verbatim. */
    val values: JSONObject? = null,
    val derived: Map<String, String> = emptyMap(),
    val envelope: Envelope? = null,
    val stat: StatSummary? = null,
    val warnings: List<String> = emptyList(),
    val busy: Boolean = false,
    val error: ApiResult.Err? = null,
) {
    val ready: Boolean get() = values != null && !busy
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
                        presets = names,
                        configsDir = r.data.optString("configs_dir"),
                    )
                }
                // Skip the synthesised "Library defaults" when real presets
                // exist — its channel is a touchstone with no file, so it is a
                // starting point for editing, not something to run as-is.
                names.firstOrNull { it != LIBRARY_DEFAULTS }
                    ?.let { select(it) }
            }
        }
    }

    fun select(name: String) = viewModelScope.launch {
        _state.update {
            it.copy(selected = name, busy = true, error = null, stat = null)
        }
        when (val p = HaloApi.preset(ctx, name)) {
            is ApiResult.Err -> _state.update { it.copy(busy = false, error = p) }
            is ApiResult.Ok -> {
                val values = p.data.optJSONObject("values") ?: JSONObject()
                _state.update { it.copy(values = values) }
                derive(values)
            }
        }
    }

    private suspend fun derive(values: JSONObject) {
        when (val d = HaloApi.derive(ctx, values)) {
            is ApiResult.Err -> _state.update { it.copy(busy = false, error = d) }
            is ApiResult.Ok -> {
                val env = d.data.optJSONObject("envelope")
                _state.update {
                    it.copy(
                        busy = false,
                        derived = d.data.optJSONObject("derived").toStringMap(),
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
        val values = _state.value.values ?: return@launch
        val previous = _state.value.stat?.handle
        _state.update { it.copy(busy = true, error = null) }

        when (val r = HaloApi.runStat(ctx, values)) {
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
    }
}

private fun JSONObject?.toStringMap(): Map<String, String> {
    if (this == null) return emptyMap()
    val out = LinkedHashMap<String, String>(length())
    keys().forEach { k -> out[k] = optString(k) }
    return out
}
