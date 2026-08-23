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

    /** First preset that is not the synthesised, deliberately-incomplete one. */
    private fun aRealPreset(): String = runBlocking {
        val schema = HaloApi.schema(ctx)
        assertTrue("schema failed: $schema", schema is ApiResult.Ok)
        val arr = (schema as ApiResult.Ok).data.getJSONArray("presets")
        (0 until arr.length()).map { arr.getString(it) }
            .first { it != "Library defaults" }
    }

    @Test
    fun presetFlowsThroughToABer() = runBlocking {
        val name = aRealPreset()

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
    fun badInputArrivesAsDataNotAsACrash() = runBlocking {
        val values = (HaloApi.preset(ctx, aRealPreset()) as ApiResult.Ok)
            .data.getJSONObject("values")
        values.put("osr", -4)          // negative oversampling -> negative dt

        val r = HaloApi.runStat(ctx, values)
        assertTrue("expected an error, got $r", r is ApiResult.Err)
        assertTrue((r as ApiResult.Err).message.isNotBlank())
    }

    @Test
    fun unknownMethodIsAlsoData() = runBlocking {
        val r = HaloApi.call(ctx, "no_such_method")
        assertTrue(r is ApiResult.Err)
        assertTrue((r as ApiResult.Err).message.contains("unknown method"))
    }
}
