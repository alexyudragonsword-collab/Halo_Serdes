package com.halo.serdes.probe.run

import android.content.Context
import com.halo.serdes.probe.api.ApiResult
import com.halo.serdes.probe.api.HaloApi
import com.halo.serdes.probe.api.stringOrNull
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Job
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch
import org.json.JSONObject

/**
 * Symbol counts, mirroring `api.QUALITY_SYMBOLS` — the labels are local.
 *
 * [estimate] is scaled from the desktop measurements (4.7 s for 20 000
 * symbols, pure Python, no numba) by the 3–5x an ARM core costs, and is
 * labelled on screen as an estimate because nothing has measured it here.
 * Its purpose is to stop someone starting the precise tier expecting seconds,
 * not to be accurate.
 */
enum class Quality(
    val key: String, val label: String, val symbols: Int, val estimate: String,
) {
    FAST("fast", "Fast", 20_000, "roughly 20 s"),
    STANDARD("standard", "Standard", 100_000, "roughly 2 min"),
    PRECISE("precise", "Precise", 500_000, "roughly 10 min"),
}

data class TimeRunState(
    val job: String? = null,
    /** queued | running | done | error | cancelled — from the Python side. */
    val state: String = "idle",
    val stage: String = "",
    val quality: Quality = Quality.FAST,
    val elapsedS: Double = 0.0,
    val cancelPending: Boolean = false,
    val handle: String? = null,
    val error: String? = null,
) {
    val active: Boolean get() = state == "queued" || state == "running"
}

/**
 * Owns the one time-domain run and its polling loop.
 *
 * A singleton rather than ViewModel state because the run has to outlive the
 * screen: that is the whole point of the foreground service, and a ViewModel
 * cleared on rotation or a back press would take the polling with it while the
 * Python thread carried on invisibly.
 *
 * The Python side already enforces one run at a time and reports its own
 * state, so this holds no duplicate notion of progress — it polls and
 * republishes.
 */
object TimeRunController {

    private val scope = CoroutineScope(SupervisorJob())
    private var poller: Job? = null

    private val _state = MutableStateFlow(TimeRunState())
    val state: StateFlow<TimeRunState> = _state.asStateFlow()

    /** Poll cadence. The stage only changes a handful of times in a run, so
     *  anything faster would just wake the interpreter for nothing. */
    private const val POLL_MS = 500L

    fun start(context: Context, values: JSONObject, quality: Quality) {
        if (_state.value.active) return
        val app = context.applicationContext
        _state.value = TimeRunState(state = "queued", quality = quality,
                                    stage = "starting")
        // Before the request, not after: the interpreter thread may already be
        // busy, and a start that waits for the first poll leaves a window in
        // which the process is unprotected while work is running.
        TimeRunService.start(app)

        poller = scope.launch {
            val payload = JSONObject()
                .put("values", values)
                .put("quality", quality.key)
            when (val r = HaloApi.call(app, "start_time_run", payload)) {
                is ApiResult.Err -> {
                    _state.value = TimeRunState(state = "error", error = r.message)
                    TimeRunService.stop(app)
                }
                is ApiResult.Ok -> {
                    val job = r.data.optString("job")
                    _state.value = _state.value.copy(job = job, state = "running")
                    pollUntilDone(app, job)
                }
            }
        }
    }

    private suspend fun pollUntilDone(context: Context, job: String) {
        while (true) {
            delay(POLL_MS)
            val r = HaloApi.call(context, "poll", JSONObject().put("job", job))
            if (r is ApiResult.Err) {
                _state.value = _state.value.copy(state = "error", error = r.message)
                break
            }
            val d = (r as ApiResult.Ok).data
            val jobErr = d.optJSONObject("job_error")?.optString("message")
            _state.value = _state.value.copy(
                state = d.optString("state"),
                stage = d.optString("stage"),
                elapsedS = d.optDouble("elapsed_s", 0.0),
                cancelPending = d.optBoolean("cancel_pending"),
                handle = d.stringOrNull("handle"),
                error = jobErr?.ifBlank { null },
            )
            if (!_state.value.active) break
        }
        TimeRunService.stop(context)
    }

    /**
     * Ask the run to stop.
     *
     * Honoured at the next stage boundary only — the receiver kernel is one
     * uninterruptible call. The state carries `cancelPending` so the screen can
     * say "stopping" instead of implying the work has already ended.
     */
    fun cancel(context: Context) {
        val job = _state.value.job ?: return
        val app = context.applicationContext
        scope.launch {
            HaloApi.call(app, "cancel", JSONObject().put("job", job))
            _state.value = _state.value.copy(cancelPending = true)
        }
    }

    /** Drop a finished run's result and reset to idle. */
    fun clear(context: Context) {
        val handle = _state.value.handle
        if (_state.value.active) return
        val app = context.applicationContext
        scope.launch {
            handle?.let { HaloApi.release(app, it) }
            _state.value = TimeRunState()
        }
    }
}
