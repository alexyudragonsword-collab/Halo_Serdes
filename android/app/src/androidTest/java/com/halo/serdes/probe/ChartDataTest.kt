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
 * What the charts are handed, on a device.
 *
 * The host suite already pins the shapes and the decimation caps. What it
 * cannot check is the trip through `org.json`: a non-finite value becomes JSON
 * null on the Python side and then `NaN` from `optDouble`, which a Canvas would
 * happily draw as a curve that silently vanishes. So the assertions here are
 * mostly about finiteness — the failure mode that only exists on this side.
 */
@RunWith(AndroidJUnit4::class)
class ChartDataTest {

    private val ctx get() = InstrumentationRegistry.getInstrumentation().targetContext

    private suspend fun aRunHandle(): Pair<String, JSONObject> {
        val schema = (HaloApi.schema(ctx) as ApiResult.Ok).data
        val name = schema.getJSONArray("presets").getString(1)
        val values = (HaloApi.preset(ctx, name) as ApiResult.Ok)
            .data.getJSONObject("values")
        val r = HaloApi.runStat(ctx, values)
        assertTrue("run_stat failed: $r", r is ApiResult.Ok)
        val d = (r as ApiResult.Ok).data
        return d.getString("handle") to d
    }

    @Test
    fun bathtubArrivesAsAPlottablePair() = runBlocking {
        val (handle, d) = aRunHandle()
        val bt = d.getJSONObject("bathtub")
        val x = bt.getJSONArray("x")
        val y = bt.getJSONArray("y")

        assertEquals("axes of different length cannot be plotted", x.length(), y.length())
        assertTrue("empty bathtub", x.length() > 0)
        for (i in 0 until x.length()) {
            assertTrue("x[$i] is not finite", x.getDouble(i).isFinite())
            val yi = y.getDouble(i)
            assertTrue("y[$i] is not finite", yi.isFinite())
            // The facade clamps at 1e-30 so a log axis always has something to
            // draw; a zero here would put log10 at -Infinity.
            assertTrue("y[$i] is not positive: $yi", yi > 0.0)
        }
        HaloApi.release(ctx, handle)
    }

    @Test
    fun eyeMapIsFiniteAndItsRangeMatchesItsContents() = runBlocking {
        val (handle, _) = aRunHandle()
        val payload = JSONObject().put("handle", handle).put("key", "stat_eye")
        val r = HaloApi.call(ctx, "series", payload)
        assertTrue("series failed: $r", r is ApiResult.Ok)
        val d = (r as ApiResult.Ok).data

        val rows = d.getInt("rows")
        val cols = d.getInt("cols")
        assertTrue("rows beyond the transport cap: $rows", rows in 1..256)
        assertTrue("cols beyond the transport cap: $cols", cols in 1..128)

        val z = d.getJSONArray("z")
        assertEquals(rows, z.length())
        val floor = d.getDouble("floor")
        var maxSeen = Double.NEGATIVE_INFINITY
        var minAbove = Double.POSITIVE_INFINITY
        for (rr in 0 until rows) {
            val row = z.getJSONArray(rr)
            assertEquals(cols, row.length())
            for (cc in 0 until cols) {
                val v = row.getDouble(cc)
                assertTrue("z[$rr][$cc] is not finite", v.isFinite())
                assertTrue("z[$rr][$cc]=$v is below the floor $floor", v >= floor - 1e-9)
                if (v > maxSeen) maxSeen = v
                if (v > floor + 1e-9 && v < minAbove) minAbove = v
            }
        }
        // The colour ramp is mapped over [zmin, zmax], so both ends have to be
        // values the data actually reaches or the picture is washed out.
        assertEquals(maxSeen, d.getDouble("zmax"), 1e-9)
        assertEquals(minAbove, d.getDouble("zmin"), 1e-9)

        HaloApi.release(ctx, handle)
    }
}
