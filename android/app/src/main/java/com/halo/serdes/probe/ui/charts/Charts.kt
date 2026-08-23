package com.halo.serdes.probe.ui.charts

import android.graphics.Bitmap
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.layout.Column
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

/**
 * A BER curve on a log10 y axis.
 *
 * The y range comes from the data, not from a fixed 1e-30..1: a bathtub for a
 * comfortable link may only span five decades, and padding it out to thirty
 * would flatten the curve into the top edge. Decade gridlines are labelled so
 * the compression stays readable.
 */
@Composable
fun LogLineChart(
    x: List<Double>,
    y: List<Double>,
    modifier: Modifier = Modifier,
    xLabel: String = "",
    yLabel: String = "",
) {
    val measurer = rememberTextMeasurer()
    val line = MaterialTheme.colorScheme.primary
    val axis = MaterialTheme.colorScheme.onSurfaceVariant
    val grid = axis.copy(alpha = 0.25f)

    val positive = y.filter { it > 0.0 }
    if (x.size != y.size || positive.isEmpty()) {
        Text("no curve to draw", style = MaterialTheme.typography.labelSmall, color = axis)
        return
    }

    val logY = y.map { if (it > 0.0) log10(it) else log10(positive.min()) }
    val yLo = floor(logY.min())
    val yHi = ceil(logY.max()).coerceAtLeast(yLo + 1.0)
    val xLo = x.min()
    val xHi = x.max().coerceAtLeast(xLo + 1e-12)

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

            // decade gridlines, one label each
            var d = yLo
            while (d <= yHi + 1e-9) {
                val yy = py(d)
                drawLine(grid, Offset(AXIS_PAD_LEFT, yy),
                         Offset(size.width - AXIS_PAD_RIGHT, yy))
                drawTinyText(measurer, "1e${d.roundToInt()}", 2f, yy - 7f, axis)
                d += 1.0
            }

            val path = Path()
            x.indices.forEach { i ->
                val xx = px(x[i]); val yy = py(logY[i])
                if (i == 0) path.moveTo(xx, yy) else path.lineTo(xx, yy)
            }
            drawPath(path, line, style = Stroke(width = 3f))

            drawTinyText(measurer, fmt(xLo), AXIS_PAD_LEFT,
                         size.height - AXIS_PAD_BOTTOM + 4f, axis)
            drawTinyText(measurer, fmt(xHi), size.width - AXIS_PAD_RIGHT - 34f,
                         size.height - AXIS_PAD_BOTTOM + 4f, axis)
        }
        if (xLabel.isNotEmpty()) {
            Text(xLabel, style = MaterialTheme.typography.labelSmall, color = axis)
        }
    }
}

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
