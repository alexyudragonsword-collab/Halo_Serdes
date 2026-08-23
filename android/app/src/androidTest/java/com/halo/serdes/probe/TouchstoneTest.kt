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
import java.io.File

/**
 * Touchstone import, on a device.
 *
 * Two things only hardware can answer. First, whether `--no-deps scikit-rf`
 * actually loads and parses here: the host check that it never needs pandas
 * was run against a desktop install, and Chaquopy resolves its own versions.
 * Second, whether the `.s4p` files staged into assets survived packaging —
 * the same class of failure that made the presets look like a config bug in
 * M0, and the reason those files were left out until there was a reader.
 *
 * The SAF picker itself is not exercised: it is a system UI, and what this
 * app owns is what happens to the bytes afterwards.
 */
@RunWith(AndroidJUnit4::class)
class TouchstoneTest {

    private val ctx get() = InstrumentationRegistry.getInstrumentation().targetContext

    /** A bundled `.s4p`, at the path the extracted data dir puts it. */
    private fun bundled(): File {
        HaloPython.start(ctx)
        val dir = File(ctx.filesDir, "halo_data/data/channels")
        val files = dir.listFiles { f -> f.name.endsWith(".s4p") } ?: emptyArray()
        assertTrue("no .s4p reached the device under $dir " +
                   "(present: ${dir.listFiles()?.joinToString { it.name }})",
                   files.isNotEmpty())
        return files.sortedBy { it.name }.first()
    }

    @Test
    fun channelFilesSurvivedThePackaging() = runBlocking<Unit> {
        val f = bundled()
        assertTrue("${f.name} is empty", f.length() > 1024)
    }

    @Test
    fun everyTouchstonePresetIsRunnableNowThatSkrfIsInstalled() = runBlocking<Unit> {
        // The point of shipping the files: before M8 the touchstone presets
        // validated and then failed in the engine, and the UI had to disable
        // Run for them. If this fails, they are back to being decoration.
        val unrunnable = TestPresets.names(ctx).filterNot {
            TestPresets.channelOk(ctx, TestPresets.values(ctx, it))
        }
        assertEquals("presets whose channel data is still unreachable",
                     emptyList<String>(), unrunnable)
    }

    @Test
    fun importReportsTheFileSpanNotJustALoss() = runBlocking<Unit> {
        val f = bundled()
        val values = TestPresets.values(ctx, TestPresets.runnable(ctx))
        val r = HaloApi.call(ctx, "import_touchstone", JSONObject()
            .put("path", f.absolutePath).put("values", values))
        assertTrue("import failed: $r", r is ApiResult.Ok)
        val d = (r as ApiResult.Ok).data

        // Every number the confirmation card shows must be present and finite;
        // a JSON null arrives as NaN from optDouble and formats as "NaN dB".
        for (k in listOf("file_f_max_ghz", "nyquist_ghz", "il_db_at_nyquist")) {
            assertTrue("$k is not finite: ${d.optDouble(k)}",
                       d.optDouble(k).isFinite())
        }
        assertTrue(d.getInt("n_freq") > 0)
        // Insertion loss is a loss: negative dB, and not a token zero.
        assertTrue("il ${d.getDouble("il_db_at_nyquist")} is not a loss",
                   d.getDouble("il_db_at_nyquist") < 0.0)
        // The flag must agree with the two numbers it summarises, or the
        // warning on the card is decoration.
        assertEquals(d.getDouble("nyquist_ghz") > d.getDouble("file_f_max_ghz"),
                     d.getBoolean("extrapolated"))
    }

    @Test
    fun aRunOnAnImportedChannelProducesARealBer() = runBlocking<Unit> {
        val f = bundled()
        val name = TestPresets.runnable(ctx)
        val values = TestPresets.values(ctx, name)
            .put("channel.kind", "touchstone")
            .put("channel.file", f.absolutePath)
        val r = HaloApi.runStat(ctx, values)
        assertTrue("run on imported channel failed: $r", r is ApiResult.Ok)
        val d = (r as ApiResult.Ok).data
        assertTrue(d.getDouble("ber").isFinite())
        HaloApi.release(ctx, d.getString("handle"))
    }

    @Test
    fun aFileThatIsNotTouchstoneFailsAsDataNotACrash() = runBlocking<Unit> {
        val junk = File(ctx.cacheDir, "not-a-network.s4p")
        junk.writeText("this is not a touchstone file\n")
        val r = HaloApi.call(ctx, "import_touchstone",
                             JSONObject().put("path", junk.absolutePath))
        // The failure must arrive through the same envelope as everything
        // else — a PyException reaching Kotlin would kill the app.
        assertTrue("expected a reported error, got $r", r is ApiResult.Err)
        assertTrue((r as ApiResult.Err).message.isNotBlank())
        junk.delete()
    }
}
