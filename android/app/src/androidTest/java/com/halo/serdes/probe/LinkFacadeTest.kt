package com.halo.serdes.probe

import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import com.halo.serdes.probe.api.ApiResult
import com.halo.serdes.probe.api.HaloApi
import kotlinx.coroutines.runBlocking
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test
import org.junit.runner.RunWith

/**
 * M4's contract, on a device: preset -> derive -> run_stat, through the same
 * Kotlin layer the screen uses.
 *
 * `PythonStackTest` proves the interpreter and the numbers. This proves the
 * path the UI actually takes — envelope parsing, the flat value map surviving
 * a JSON round trip, and failures arriving as data.
 */
@RunWith(AndroidJUnit4::class)
class LinkFacadeTest {

    private val ctx get() = InstrumentationRegistry.getInstrumentation().targetContext

    @Test
    fun presetFlowsThroughToABer() = runBlocking<Unit> {
        val name = TestPresets.runnable(ctx)

        val preset = HaloApi.preset(ctx, name)
        assertTrue("preset failed: $preset", preset is ApiResult.Ok)
        val values = (preset as ApiResult.Ok).data.getJSONObject("values")
        assertTrue("preset carried no values", values.length() > 10)

        val derive = HaloApi.derive(ctx, values)
        assertTrue("derive failed: $derive", derive is ApiResult.Ok)
        val d = (derive as ApiResult.Ok).data
        assertTrue("preset did not validate", d.getBoolean("valid"))
        // Derived quantities are what tell the user the preset took effect.
        assertTrue(d.getJSONObject("derived").getString("UI").endsWith("ps"))

        val run = HaloApi.runStat(ctx, values)
        assertTrue("run_stat failed: $run", run is ApiResult.Ok)
        val r = (run as ApiResult.Ok).data
        val ber = r.getDouble("ber")
        assertTrue("BER out of range: $ber", ber >= 0.0 && ber < 1.0)
        assertTrue("no bathtub", r.getJSONObject("bathtub").getJSONArray("x").length() > 0)

        // Releasing must be honoured, or every run leaks its numpy arrays.
        val handle = r.getString("handle")
        assertTrue(handle.isNotEmpty())
        val released = HaloApi.release(ctx, handle)
        assertTrue("release failed: $released", released is ApiResult.Ok)
        assertEquals(handle, (released as ApiResult.Ok).data.getString("released"))
    }

    /**
     * A bad value must come back as a rendered card, not a crash.
     *
     * This is the property the whole facade is built around: a Python exception
     * crossing JNI would surface as an opaque PyException, so the UI could show
     * nothing useful and would likely take the process with it.
     */
    @Test
    fun badInputArrivesAsDataNotAsACrash() = runBlocking<Unit> {
        val values = TestPresets.values(ctx, TestPresets.runnable(ctx))
        values.put("osr", -4)          // negative oversampling -> negative dt

        val r = HaloApi.runStat(ctx, values)
        assertTrue("expected an error, got $r", r is ApiResult.Err)
        assertTrue((r as ApiResult.Err).message.isNotBlank())
        // `field` is what the form uses to mark an input red, and the facade
        // sends it as an explicit JSON null when no path was identified.
        // org.json's optString turns that into the string "null", which put a
        // red ring around a field by that name. Null must stay null.
        assertTrue("field should be null or a real path, was ${r.field}",
                   r.field == null || r.field in TestPresets.values(ctx,
                       TestPresets.runnable(ctx)).keys().asSequence().toSet())
    }

    /**
     * An unreachable channel must be reported by `derive`, not discovered by
     * running the engine.
     *
     * This is the contract that the packaging decision rests on: shipping the
     * presets without their `.s4p` files is fine *provided* the client can
     * tell which ones are usable before offering them. Asserted as an
     * implication so it stays true on a build that does bundle the files.
     */
    @Test
    fun unreachableChannelsAreFlaggedBeforeRunning() = runBlocking<Unit> {
        val flagged = TestPresets.names(ctx).firstOrNull { !TestPresets.channelOk(ctx, TestPresets.values(ctx, it)) }
            ?: return@runBlocking      // every channel present: nothing to check
        val r = HaloApi.runStat(ctx, TestPresets.values(ctx, flagged))
        assertTrue("$flagged was flagged unusable yet ran: $r", r is ApiResult.Err)
    }

    @Test
    fun unknownMethodIsAlsoData() = runBlocking<Unit> {
        val r = HaloApi.call(ctx, "no_such_method")
        assertTrue(r is ApiResult.Err)
        assertTrue((r as ApiResult.Err).message.contains("unknown method"))
    }
}
