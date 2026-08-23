package com.halo.serdes.probe.ui.charts

import android.graphics.Bitmap
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.remember
import androidx.compose.ui.Modifier
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.FilterQuality
import androidx.compose.ui.graphics.ImageBitmap
import androidx.compose.ui.graphics.Path
import androidx.compose.ui.graphics.asImageBitmap
import androidx.compose.ui.graphics.drawscope.DrawScope
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.graphics.toArgb
import androidx.compose.ui.text.TextMeasurer
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.drawText
import androidx.compose.ui.text.rememberTextMeasurer
import androidx.compose.ui.unit.IntOffset
import androidx.compose.ui.unit.IntSize
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import kotlin.math.ceil
import kotlin.math.floor
import kotlin.math.log10
import kotlin.math.roundToInt

/**
 * Charts drawn straight onto a Compose Canvas.
 *
 * No charting library: the shapes needed here are few and specific (a
 * log-decade bathtub, a density map), and a general library would arrive with
 * its own version-matching risk for the sake of features this never uses.
 */

private const val AXIS_PAD_LEFT = 46f
private const val AXIS_PAD_BOTTOM = 22f
private const val AXIS_PAD_TOP = 8f
private const val AXIS_PAD_RIGHT = 8f

/** One named curve in a panel. */
data class Series(val label: String, val y: List<Double>)

/**
 * A BER curve on a log10 y axis. The bathtub's entry point.
 *
 * A one-series shorthand for [MultiLineChart]; the axis handling is shared so
 * a fix to decade labelling reaches both.
 */
@Composable
fun LogLineChart(
    x: List<Double>,
    y: List<Double>,
    modifier: Modifier = Modifier,
    xLabel: String = "",
    yLabel: String = "",
) = MultiLineChart(x, listOf(Series("", y)), modifier, xLabel, yLabel,
                   xLog = false, yLog = true)

/**
 * N curves sharing one pair of axes, either of which may be logarithmic.
 *
 * The axis choices are not made here. They come from `studies.STUDY_PLOTS`,
 * next to the code that produces the data, because whether a series belongs on
 * a log axis is a property of the quantity — and a client guessing would guess
 * differently from the desktop one.
 *
 * Ranges come from the data. A fixed 1e-30..1 would flatten a comfortable
 * link's five-decade bathtub into the top edge.
 */
@Composable
fun MultiLineChart(
    x: List<Double>,
    series: List<Series>,
    modifier: Modifier = Modifier,
    xLabel: String = "",
    yLabel: String = "",
    xLog: Boolean = false,
    yLog: Boolean = false,
) {
    val measurer = rememberTextMeasurer()
    val axis = MaterialTheme.colorScheme.onSurfaceVariant
    val grid = axis.copy(alpha = 0.25f)
    val palette = seriesPalette()

    // Points a log axis cannot represent are dropped, not clamped: a clamped
    // zero draws a line down to the floor that no measurement supports.
    val drawable = series.map { s ->
        s to x.indices.filter { i ->
            i < s.y.size && s.y[i].isFinite() && x[i].isFinite() &&
                (!yLog || s.y[i] > 0.0) && (!xLog || x[i] > 0.0)
        }
    }.filter { it.second.size >= 2 }

    if (drawable.isEmpty()) {
        Text("no curve to draw", style = MaterialTheme.typography.labelSmall, color = axis)
        return
    }

    fun tx(v: Double) = if (xLog) log10(v) else v
    fun ty(v: Double) = if (yLog) log10(v) else v

    val allX = drawable.flatMap { (_, idx) -> idx.map { tx(x[it]) } }
    val allY = drawable.flatMap { (s, idx) -> idx.map { ty(s.y[it]) } }
    val xLo = allX.min()
    val xHi = allX.max().coerceAtLeast(xLo + 1e-12)
    val yLo = if (yLog) floor(allY.min()) else allY.min()
    val yHi = if (yLog) ceil(allY.max()).coerceAtLeast(yLo + 1.0)
              else allY.max().coerceAtLeast(yLo + 1e-12)

    Column(modifier) {
        if (yLabel.isNotEmpty()) {
            Text(yLabel, style = MaterialTheme.typography.labelSmall, color = axis)
        }
        Canvas(Modifier.fillMaxWidth().height(180.dp).padding(top = 2.dp)) {
            val plotW = size.width - AXIS_PAD_LEFT - AXIS_PAD_RIGHT
            val plotH = size.height - AXIS_PAD_TOP - AXIS_PAD_BOTTOM
            fun px(v: Double) = AXIS_PAD_LEFT + ((v - xLo) / (xHi - xLo)).toFloat() * plotW
            fun py(v: Double) =
                AXIS_PAD_TOP + (1.0 - (v - yLo) / (yHi - yLo)).toFloat() * plotH

            for ((value, label) in yGridlines(yLo, yHi, yLog)) {
                val yy = py(value)
                drawLine(grid, Offset(AXIS_PAD_LEFT, yy),
                         Offset(size.width - AXIS_PAD_RIGHT, yy))
                drawTinyText(measurer, label, 2f, yy - 7f, axis)
            }

            drawable.forEachIndexed { n, (s, idx) ->
                val path = Path()
                idx.forEachIndexed { k, i ->
                    val xx = px(tx(x[i])); val yy = py(ty(s.y[i]))
                    if (k == 0) path.moveTo(xx, yy) else path.lineTo(xx, yy)
                }
                drawPath(path, palette[n % palette.size], style = Stroke(width = 3f))
            }

            val loLabel = if (xLog) fmt(Math.pow(10.0, xLo)) else fmt(xLo)
            val hiLabel = if (xLog) fmt(Math.pow(10.0, xHi)) else fmt(xHi)
            drawTinyText(measurer, loLabel, AXIS_PAD_LEFT,
                         size.height - AXIS_PAD_BOTTOM + 4f, axis)
            drawTinyText(measurer, hiLabel, size.width - AXIS_PAD_RIGHT - 40f,
                         size.height - AXIS_PAD_BOTTOM + 4f, axis)
        }
        if (xLabel.isNotEmpty()) {
            Text(xLabel, style = MaterialTheme.typography.labelSmall, color = axis)
        }
        // A legend only where it carries information. One unnamed curve is
        // already identified by the y-axis label above it.
        if (drawable.size > 1 || drawable.first().first.label.isNotEmpty()) {
            Row(Modifier.fillMaxWidth().padding(top = 2.dp),
                horizontalArrangement = Arrangement.spacedBy(10.dp)) {
                drawable.forEachIndexed { n, (s, _) ->
                    Text(s.label, style = MaterialTheme.typography.labelSmall,
                         color = palette[n % palette.size])
                }
            }
        }
    }
}

/**
 * Gridline positions and their labels.
 *
 * Log axes get one line per decade, which is what makes a compressed BER
 * curve readable. Linear axes get a fixed small count — the quantities on
 * them here (dB, mV) have no natural decade structure to follow.
 */
private fun yGridlines(lo: Double, hi: Double, log: Boolean): List<Pair<Double, String>> {
    if (log) {
        // A wide span would otherwise draw a line every few pixels.
        val step = maxOf(1.0, ceil((hi - lo) / 8.0))
        val out = ArrayList<Pair<Double, String>>()
        var d = lo
        while (d <= hi + 1e-9) {
            out += d to "1e${d.roundToInt()}"
            d += step
        }
        return out
    }
    return (0..4).map { k ->
        val v = lo + (hi - lo) * k / 4.0
        v to fmt(v)
    }
}

@Composable
private fun seriesPalette(): List<Color> = listOf(
    MaterialTheme.colorScheme.primary,
    MaterialTheme.colorScheme.tertiary,
    MaterialTheme.colorScheme.error,
    MaterialTheme.colorScheme.secondary,
)

/**
 * The statistical eye, as a density image.
 *
 * Drawn as one scaled bitmap rather than a rect per cell: a 256x128 map is
 * 32768 cells, and issuing that many draw calls every frame is what makes a
 * chart stutter. The bitmap is built once per dataset and only rescaled.
 *
 * `floor` cells are painted as background, not as the darkest colour in the
 * ramp. They mean "no probability resolved on this grid" — the opposite of a
 * very small probability — and colouring them like data reads as a filled eye
 * where there is nothing.
 */
@Composable
fun EyeHeatMap(
    key: String,
    z: List<List<Double>>,
    zmin: Double,
    zmax: Double,
    floorValue: Double,
    modifier: Modifier = Modifier,
) {
    val axis = MaterialTheme.colorScheme.onSurfaceVariant
    if (z.isEmpty() || z[0].isEmpty()) {
        Text("no eye to draw", style = MaterialTheme.typography.labelSmall, color = axis)
        return
    }
    val background = MaterialTheme.colorScheme.surfaceVariant

    // Keyed on the run handle, not on `z`: comparing a 256x128 nested list for
    // equality on every recomposition costs more than the drawing does.
    val image: ImageBitmap = remember(key, background) {
        buildDensityImage(z, zmin, zmax, floorValue, background)
    }
    Canvas(modifier.fillMaxWidth().height(200.dp)) {
        drawImage(
            image = image,
            srcOffset = IntOffset.Zero,
            srcSize = IntSize(image.width, image.height),
            dstOffset = IntOffset.Zero,
            dstSize = IntSize(size.width.roundToInt(), size.height.roundToInt()),
            // The map is only `osr` columns wide (16 for a typical preset), so
            // nearest-neighbour would show it as a row of hard bars. Smoothing
            // is honest here: the underlying PDF is continuous in time.
            filterQuality = FilterQuality.Low,
        )
    }
}

private fun buildDensityImage(
    z: List<List<Double>>,
    zmin: Double,
    zmax: Double,
    floorValue: Double,
    background: Color,
): ImageBitmap {
    val rows = z.size
    val cols = z[0].size
    val span = (zmax - zmin).takeIf { it > 1e-12 } ?: 1.0
    val pixels = IntArray(rows * cols)
    val bg = background.toArgb()

    for (r in 0 until rows) {
        val row = z[r]
        for (c in 0 until cols) {
            val v = row.getOrElse(c) { floorValue }
            pixels[r * cols + c] =
                if (v <= floorValue + 1e-9) bg
                else densityColor(((v - zmin) / span).coerceIn(0.0, 1.0)).toArgb()
        }
    }
    val bmp = Bitmap.createBitmap(cols, rows, Bitmap.Config.ARGB_8888)
    bmp.setPixels(pixels, 0, cols, 0, 0, cols, rows)
    return bmp.asImageBitmap()
}

/**
 * Dark blue -> cyan -> yellow, i.e. rising density reads as rising brightness.
 *
 * Deliberately monotonic in luminance: a rainbow ramp invents banding that
 * looks like structure in the data, which on an eye skirt is exactly the sort
 * of artefact someone would try to explain.
 */
private fun densityColor(t: Double): Color {
    val f = t.coerceIn(0.0, 1.0)
    return when {
        f < 0.5 -> {
            val u = (f / 0.5).toFloat()
            Color(red = 0.05f * (1 - u) + 0.0f * u,
                  green = 0.05f * (1 - u) + 0.65f * u,
                  blue = 0.35f * (1 - u) + 0.75f * u)
        }
        else -> {
            val u = ((f - 0.5) / 0.5).toFloat()
            Color(red = 0.0f * (1 - u) + 0.98f * u,
                  green = 0.65f * (1 - u) + 0.92f * u,
                  blue = 0.75f * (1 - u) + 0.25f * u)
        }
    }
}

private fun DrawScope.drawTinyText(
    measurer: TextMeasurer, text: String, x: Float, y: Float, color: Color,
) {
    drawText(
        textMeasurer = measurer,
        text = text,
        topLeft = Offset(x, y),
        style = TextStyle(color = color, fontSize = 9.sp),
    )
}

private fun fmt(v: Double): String =
    if (kotlin.math.abs(v) >= 100 || (v != 0.0 && kotlin.math.abs(v) < 0.01))
        "%.1e".format(v) else "%.3g".format(v)
