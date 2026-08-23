package com.halo.serdes.probe

import android.content.Context
import com.halo.serdes.probe.api.ApiResult
import com.halo.serdes.probe.api.HaloApi
import org.json.JSONObject

/**
 * Choosing a preset for a test, in one place.
 *
 * "Take the first preset" is wrong on this build and had been written twice by
 * the time it was noticed: the APK ships the YAML presets but not the 4.4 MB
 * of `.s4p` channel files, so the touchstone ones validate perfectly and then
 * fail the moment an engine runs. The first time round it read as an app bug;
 * it was the test asking for the impossible.
 *
 * So: [names] for anything that only inspects config, [runnable] for anything
 * that will actually compute.
 */
object TestPresets {

    private const val SYNTHESISED = "Library defaults"

    /** Real presets only — "Library defaults" is synthesised and incomplete. */
    suspend fun names(ctx: Context): List<String> {
        val schema = HaloApi.schema(ctx) as ApiResult.Ok
        val arr = schema.data.getJSONArray("presets")
        return (0 until arr.length()).map { arr.getString(it) }
            .filter { it != SYNTHESISED }
    }

    suspend fun values(ctx: Context, name: String): JSONObject =
        (HaloApi.preset(ctx, name) as ApiResult.Ok).data.getJSONObject("values")

    /** True when `derive` says this config's channel data is reachable here. */
    suspend fun channelOk(ctx: Context, values: JSONObject): Boolean {
        val d = HaloApi.derive(ctx, values) as? ApiResult.Ok ?: return false
        val ch = d.data.optJSONObject("channel") ?: return true
        return ch.optBoolean("ok", true)
    }

    /** The first preset that can actually run on this device. */
    suspend fun runnable(ctx: Context): String {
        val all = names(ctx)
        return all.firstOrNull { channelOk(ctx, values(ctx, it)) }
            ?: error("no preset can run here — presets: $all")
    }
}
