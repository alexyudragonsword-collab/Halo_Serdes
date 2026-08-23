package com.halo.serdes.probe

import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import com.halo.serdes.probe.api.ApiResult
import com.halo.serdes.probe.api.HaloApi
import com.halo.serdes.probe.ui.parsePlots
import com.halo.serdes.probe.ui.parseStudies
import com.halo.serdes.probe.ui.parseStudyData
import kotlinx.coroutines.runBlocking
import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test
import org.junit.runner.RunWith

/**
 * The studies tab renders whatever the facade advertises, so what has to hold
 * on a device is that the advertisement and the data agree after the trip
 * through `org.json`.
 *
 * Deliberately not a list of study names. Pinning them here would put the very
 * hardcoding this milestone removed back into the test — and the test would
 * then pass while the screen showed nothing.
 */
@RunWith(AndroidJUnit4::class)
class StudyContractTest {

    private val ctx get() = InstrumentationRegistry.getInstrumentation().targetContext

    @Test
    fun everyAdvertisedStudyParsesIntoARenderableSpec() = runBlocking<Unit> {
        val schema = (HaloApi.schema(ctx) as ApiResult.Ok).data
        val studies = parseStudies(schema)
        assertTrue("no studies advertised", studies.isNotEmpty())
        for (s in studies) {
            assertTrue("blank title for ${s.name}", s.title.isNotBlank())
            assertTrue("blank blurb for ${s.name}", s.blurb.isNotBlank())
            assertTrue("${s.name} has no plot spec", s.plots.isNotEmpty())
            for (p in s.plots) {
                assertTrue("${s.name}: panel has no x", p.x.isNotBlank())
                assertTrue("${s.name}: panel has no y", p.y.isNotEmpty())
            }
        }
    }

    /**
     * The cheapest study, run for real.
     *
     * `fec` is config-independent, so it needs no engine and no channel — the
     * one sweep that can be run on an emulator without minutes of compute.
     * It is also the one whose x axis is declared logarithmic and whose
     * `concat` series underflows to exact zero, which is the case the chart
     * would otherwise be unable to draw at all.
     */
    @Test
    fun aSweepReturnsDataThatMatchesItsOwnPlotSpec() = runBlocking<Unit> {
        val values = TestPresets.values(ctx, TestPresets.runnable(ctx))
        val r = HaloApi.call(ctx, "study",
            JSONObject().put("name", "fec").put("values", values))
        assertTrue("study failed: $r", r is ApiResult.Ok)
        val d = (r as ApiResult.Ok).data

        val plots = parsePlots(d.optJSONArray("plots"))
        val data = parseStudyData(d.optJSONObject("data"))
        assertTrue("no panels", plots.isNotEmpty())

        for (p in plots) {
            val x = data[p.x]
            assertTrue("spec names x=${p.x}, data has ${data.keys}", x != null)
            for (key in p.y) {
                val y = data[key]
                assertTrue("spec names y=$key, data has ${data.keys}", y != null)
                assertEquals("$key is not aligned with ${p.x}", x!!.size, y!!.size)
                // A log axis cannot draw a zero or a negative. The facade
                // clamps declared-log series to 1e-300 for exactly this; if
                // that stopped happening the panel would silently lose points.
                if (p.yLog) {
                    assertTrue("$key has non-positive values on a log axis",
                               y.all { !it.isFinite() || it > 0.0 })
                }
            }
            if (p.xLog) {
                assertTrue("${p.x} has non-positive values on a log axis",
                           x!!.all { !it.isFinite() || it > 0.0 })
            }
        }
    }
}
