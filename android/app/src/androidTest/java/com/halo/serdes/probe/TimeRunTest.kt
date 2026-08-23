package com.halo.serdes.probe

import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import com.halo.serdes.probe.api.ApiResult
import com.halo.serdes.probe.api.HaloApi
import kotlinx.coroutines.runBlocking
import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test
import org.junit.runner.RunWith

/**
 * The long-run path, on a device.
 *
 * The host suite already pins the job lifecycle. What it cannot check is that
 * a run started from Kotlin survives on the single interpreter thread while
 * the *same* thread keeps answering `poll` — if `start_time_run` blocked that
 * thread, every poll would queue behind it and the UI would freeze for the
 * whole run while showing no progress at all. That is a property of this
 * side's dispatcher, not of Python's threading.
 */
@RunWith(AndroidJUnit4::class)
class TimeRunTest {

    private val ctx get() = InstrumentationRegistry.getInstrumentation().targetContext

    private suspend fun start(quality: String): String {
        val values = TestPresets.values(ctx, TestPresets.runnable(ctx))
        val payload = JSONObject().put("values", values).put("quality", quality)
        val r = HaloApi.call(ctx, "start_time_run", payload)
        assertTrue("start failed: $r", r is ApiResult.Ok)
        return (r as ApiResult.Ok).data.getString("job")
    }

    private suspend fun poll(job: String): JSONObject =
        (HaloApi.call(ctx, "poll", JSONObject().put("job", job)) as ApiResult.Ok).data

    private suspend fun await(job: String, timeoutMs: Long): JSONObject {
        val deadline = System.currentTimeMillis() + timeoutMs
        while (System.currentTimeMillis() < deadline) {
            val p = poll(job)
            if (p.getString("state") in setOf("done", "error", "cancelled")) return p
            kotlinx.coroutines.delay(200)
        }
        throw AssertionError("job $job did not finish in ${timeoutMs}ms")
    }

    @Test
    fun pollingStaysResponsiveWhileTheRunIsInFlight() = runBlocking<Unit> {
        val job = start("fast")
        // The run is on a Python thread; these polls go through the same
        // single-thread dispatcher the run was started from. Getting answers
        // at all is the assertion — a blocked dispatcher would time out here.
        val p = await(job, 300_000)
        assertEquals("done", p.getString("state"))
        assertTrue(p.getDouble("elapsed_s") > 0.0)

        val handle = p.getString("handle")
        val res = (HaloApi.call(ctx, "result", JSONObject().put("handle", handle))
            as ApiResult.Ok).data
        val sim = res.getJSONObject("sim")
        assertEquals(20_000, sim.getInt("n_requested"))
        assertTrue(sim.getInt("n_checked") > 0)
        // The invariant the result card is built on: a zero-error run must say
        // its BER is an upper bound, never present 0.0 as a measurement.
        assertEquals(sim.getInt("n_errors") == 0, sim.getBoolean("ber_is_upper_bound"))
        // Every series it advertises must actually be fetchable.
        val series = res.getJSONArray("series")
        for (i in 0 until series.length()) {
            val key = series.getString(i)
            val s = HaloApi.call(ctx, "series", JSONObject()
                .put("handle", handle).put("key", key).put("max_points", 16))
            assertTrue("advertised series $key is not fetchable: $s", s is ApiResult.Ok)
        }
        HaloApi.release(ctx, handle)
    }

    /**
     * A stopped run must not be reported as a broken one.
     *
     * The outcome is deliberately not pinned to "cancelled". Cancellation is
     * honoured at a stage boundary, and the only boundary before the engine is
     * ~5 ms after the job starts — less than one round trip through this side's
     * dispatcher. So whether the flag arrives in time is a race this test
     * cannot win reliably, and the honest assertion is the one that was
     * actually broken once: a cancelled run came back as `error`, because
     * `run_link` swallowed the cancellation along with real failures.
     *
     * The fast tier, not precise: losing the race on `precise` would mean
     * waiting out 500 000 symbols on an emulator.
     */
    @Test
    fun cancellingIsNeverReportedAsAFailure() = runBlocking<Unit> {
        val job = start("fast")
        assertTrue(HaloApi.call(ctx, "cancel", JSONObject().put("job", job))
                       is ApiResult.Ok)
        val p = await(job, 300_000)
        assertTrue("stopped run reported as $p",
                   p.getString("state") in setOf("cancelled", "done"))
        if (p.getString("state") == "cancelled") {
            // Nothing was measured, so there must be no result to read.
            assertTrue(p.optString("handle").isBlank())
        } else {
            HaloApi.release(ctx, p.getString("handle"))
        }
    }

    @Test
    fun onlyOneRunAtATime() = runBlocking<Unit> {
        val first = start("fast")
        try {
            val values = TestPresets.values(ctx, TestPresets.runnable(ctx))
            val second = HaloApi.call(ctx, "start_time_run",
                JSONObject().put("values", values).put("quality", "fast"))
            // Two runs would share one interpreter and thrash one registry for
            // no gain, so the second must be refused as data, not queued.
            assertTrue("second start was accepted: $second", second is ApiResult.Err)
            assertTrue((second as ApiResult.Err).message.contains("already in progress"))
        } finally {
            HaloApi.call(ctx, "cancel", JSONObject().put("job", first))
            val p = await(first, 300_000)
            if (p.getString("state") == "done") {
                HaloApi.release(ctx, p.getString("handle"))
            }
        }
    }
}
