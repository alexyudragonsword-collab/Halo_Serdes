package com.halo.serdes.probe

import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test
import org.junit.runner.RunWith

/**
 * The M0 verdict, as an automated test.
 *
 * Running this on a CI emulator turns "will Chaquopy actually work?" from a
 * question needing a phone in someone's hand into a question the build answers
 * on every push. It cannot cover everything a device would — an emulator is
 * x86_64, so ARM float behaviour and 16 KB-page loading still need real
 * hardware — but it does prove the wheels install, load, and compute.
 */
@RunWith(AndroidJUnit4::class)
class PythonStackTest {

    private fun report(): JSONObject {
        val ctx = InstrumentationRegistry.getInstrumentation().targetContext
        return JSONObject(HaloPython.probeJson(ctx))
    }

    @Test
    fun numpyAndScipyLoadAndCompute() {
        val r = report()
        assertTrue("probe raised: ${r.opt("error")}", !r.has("error"))

        val v = r.getJSONObject("versions")
        for (mod in listOf("numpy", "scipy")) {
            val ver = v.getString(mod)
            assertTrue("$mod missing on device: $ver", !ver.startsWith("MISSING"))
        }
        // numba must be absent — invariant #4 says the pure-Python kernels are
        // the correctness reference, and this build ships no numba wheel
        assertTrue("numba unexpectedly present", v.getBoolean("numba_absent"))

        val s = r.getJSONObject("scipy")
        assertEquals(0.15729920705028516, s.getDouble("erfc(1.0)"), 1e-12)
    }

    @Test
    fun computeCoreRunsEndToEnd() {
        val c = report().getJSONObject("compute")
        val ber = c.getDouble("ber")
        assertTrue("BER out of range: $ber", ber > 0.0 && ber < 1.0)
        assertTrue("COM implausible: ${c.getDouble("com_db")}",
                   c.getDouble("com_db") > -50.0 && c.getDouble("com_db") < 50.0)
        assertTrue(c.getInt("bathtub_points") > 0)
    }

    @Test
    fun matchesTheGoldenValuesComputedOnTheHost() {
        val r = report()
        val g = r.getJSONObject("golden")
        assertTrue("no golden bundled — run android/tools/gen_probe_golden.py " +
                   "before assembling", g.getBoolean("checked"))
        assertTrue("device diverges from host: ${g.getJSONObject("mismatches")}",
                   g.getBoolean("match"))
    }

    @Test
    fun jsonFacadeIsReachableFromKotlin() {
        val ctx = InstrumentationRegistry.getInstrumentation().targetContext
        val schema = JSONObject(HaloPython.call(ctx, "schema"))
        assertTrue(schema.getBoolean("ok"))
        assertTrue(schema.getJSONObject("data").getJSONArray("sections").length() > 0)

        // failures must arrive as data, not as a PyException
        val bad = JSONObject(HaloPython.call(ctx, "no_such_method"))
        assertTrue(!bad.getBoolean("ok"))
        assertTrue(bad.getJSONObject("error").getString("message").isNotEmpty())
    }
}
