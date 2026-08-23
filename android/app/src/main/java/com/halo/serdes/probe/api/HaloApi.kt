package com.halo.serdes.probe.api

import android.content.Context
import com.halo.serdes.probe.HaloPython
import org.json.JSONArray
import org.json.JSONObject

/**
 * Kotlin side of the JSON facade.
 *
 * `halo_serdes_app.api.call` already guarantees one shape for every response
 * — `{ok, data, warnings, ms}` or `{ok:false, error:{kind, message, field}}`
 * — so the envelope is unwrapped exactly once, here. Screens receive an
 * [ApiResult] and never touch `ok`, never re-read `error.message`, and never
 * see a `PyException`.
 *
 * Every entry point is `suspend` and hops to [HaloPython.dispatcher], so there
 * is no way to call Python from the main thread by accident.
 */
sealed interface ApiResult {

    data class Ok(
        val data: JSONObject,
        val warnings: List<String> = emptyList(),
        val ms: Double = 0.0,
    ) : ApiResult

    /**
     * A failure the Python side chose to report as data.
     *
     * [field] is a dotted config path when the message named one, which is what
     * lets a form mark the offending input instead of showing a dialog.
     */
    data class Err(
        val kind: String,
        val message: String,
        val field: String? = null,
        val traceback: String? = null,
    ) : ApiResult
}

object HaloApi {

    /** One raw call. Returns the `data` object, or the error, never throws. */
    suspend fun call(
        context: Context,
        method: String,
        payload: JSONObject = JSONObject(),
    ): ApiResult = HaloPython.on(context) {
        val raw = try {
            HaloPython.callOnThisThread(method, payload.toString())
        } catch (t: Throwable) {
            // Only reachable if the bridge itself fails (interpreter dead,
            // module missing) — api.call never lets a Python exception out.
            return@on ApiResult.Err("bridge", "${t::class.java.simpleName}: ${t.message}")
        }
        parse(raw)
    }

    /** Split out so it can be unit-tested against recorded responses. */
    fun parse(raw: String): ApiResult = try {
        val env = JSONObject(raw)
        if (env.optBoolean("ok")) {
            ApiResult.Ok(
                data = env.optJSONObject("data") ?: JSONObject(),
                warnings = env.optJSONArray("warnings").toStringList(),
                ms = env.optDouble("ms", 0.0),
            )
        } else {
            val e = env.optJSONObject("error") ?: JSONObject()
            ApiResult.Err(
                kind = e.optString("kind", "error"),
                message = e.optString("message", raw.take(400)),
                field = e.optString("field").ifBlank { null },
                traceback = e.optString("traceback").ifBlank { null },
            )
        }
    } catch (t: Throwable) {
        ApiResult.Err("protocol", "malformed response: ${raw.take(400)}")
    }

    // ------------------------------------------------------------ methods ---

    /** Form spec, preset names, and the directory the presets came from. */
    suspend fun schema(context: Context) = call(context, "schema")

    /** A preset's flat `{path: value}` map, ready to hand back to `run_stat`. */
    suspend fun preset(context: Context, name: String) =
        call(context, "preset", JSONObject().put("name", name))

    /** Validate and derive, without running an engine (sub-millisecond). */
    suspend fun derive(context: Context, values: JSONObject) =
        call(context, "derive", JSONObject().put("values", values))

    /** Statistical engine — the interactive path. */
    suspend fun runStat(context: Context, values: JSONObject) =
        call(context, "run_stat", JSONObject().put("values", values))

    /** Drop a stored result so its numpy arrays can be collected. */
    suspend fun release(context: Context, handle: String) =
        call(context, "release", JSONObject().put("handle", handle))
}

internal fun JSONArray?.toStringList(): List<String> =
    if (this == null) emptyList() else List(length()) { optString(it) }
