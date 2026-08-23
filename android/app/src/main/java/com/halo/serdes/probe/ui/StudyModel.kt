package com.halo.serdes.probe.ui

import org.json.JSONObject

/**
 * One plot panel, exactly as `studies.STUDY_PLOTS` describes it.
 *
 * Nothing here decides how a study is drawn. Which key is the x axis, which
 * series belong together, and which axes are logarithmic all come from the
 * Python side — the same reasoning as `SECTIONS` for the parameter form. A
 * study added to `studies.py` with a plot spec appears here with no Kotlin
 * change, and one added without a spec shows its numbers rather than a
 * guessed chart.
 */
data class PlotSpec(
    val x: String,
    val y: List<String>,
    val xLabel: String,
    val yLabel: String,
    val xLog: Boolean,
    val yLog: Boolean,
)

/** One study as the facade advertises it: name, wording, and panel specs. */
data class StudyMeta(
    val name: String,
    val title: String,
    val blurb: String,
    val plots: List<PlotSpec>,
)

fun parseStudies(o: JSONObject): List<StudyMeta> {
    val arr = o.optJSONArray("studies") ?: return emptyList()
    return (0 until arr.length()).mapNotNull { i ->
        val s = arr.optJSONObject(i) ?: return@mapNotNull null
        StudyMeta(
            name = s.optString("name"),
            title = s.optString("title").ifBlank { s.optString("name") },
            blurb = s.optString("blurb"),
            plots = parsePlots(s.optJSONArray("plots")),
        )
    }.filter { it.name.isNotBlank() }
}

/** A finished study: its panels and the flat `{name: array}` it returned. */
data class StudyResult(
    val name: String,
    val plots: List<PlotSpec>,
    val data: Map<String, List<Double>>,
    /** Set when the study declined this config — information, not a failure. */
    val note: String? = null,
    val handle: String? = null,
)

fun parsePlots(arr: org.json.JSONArray?): List<PlotSpec> {
    if (arr == null) return emptyList()
    return (0 until arr.length()).mapNotNull { i ->
        val o = arr.optJSONObject(i) ?: return@mapNotNull null
        val ys = o.optJSONArray("y") ?: return@mapNotNull null
        PlotSpec(
            x = o.optString("x"),
            y = (0 until ys.length()).map { ys.optString(it) },
            xLabel = o.optString("x_label"),
            yLabel = o.optString("y_label"),
            xLog = o.optBoolean("x_log"),
            yLog = o.optBoolean("y_log"),
        )
    }
}

/** Every numeric array in a study's flat result, keyed by name. */
fun parseStudyData(o: JSONObject?): Map<String, List<Double>> {
    if (o == null) return emptyMap()
    val out = LinkedHashMap<String, List<Double>>()
    o.keys().forEach { k ->
        val arr = o.optJSONArray(k) ?: return@forEach
        // optDouble yields NaN for a JSON null, which is what a non-finite
        // Python value becomes. Kept rather than dropped: the chart filters
        // per point, so dropping here would silently shift the x alignment.
        out[k] = List(arr.length()) { arr.optDouble(it) }
    }
    return out
}
