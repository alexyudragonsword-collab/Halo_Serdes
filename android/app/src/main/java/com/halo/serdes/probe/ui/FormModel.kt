package com.halo.serdes.probe.ui

import org.json.JSONObject

/**
 * One editable parameter, straight from the Python side's `SECTIONS`.
 *
 * Nothing here is declared twice: the label, the widget kind, the unit scale
 * and the enum options all come from `config_bridge.SECTIONS`, which is also
 * what the Dash app renders. Adding a field to the config schema makes it
 * appear on the phone with no Kotlin change at all — the reason this layer
 * exists rather than a hand-written form.
 */
data class FormField(
    val path: String,
    val label: String,
    val kind: String,
    val options: List<String> = emptyList(),
) {
    /** Optional fields may be left blank; the loader maps that back to None. */
    val optional: Boolean get() = kind.startsWith("opt_")

    /** True where the value crosses the boundary as a JSON boolean, not a string. */
    val isBool: Boolean get() = kind == "bool"
}

/**
 * Kinds `FieldRow` knows how to draw.
 *
 * Asserted against the live schema by `FormContractTest`: a kind added to the
 * config layer with no widget here would otherwise render as a bare text box
 * (or, for something list-shaped, silently mangle the value) with nothing to
 * flag it.
 */
val RENDERABLE_KINDS = setOf(
    "float", "int", "bool", "enum", "str", "opt_str",
    "opt_float", "opt_int", "tuple_float", "opt_tuple_float",
)

data class FormSection(
    val id: String,
    val title: String,
    val fields: List<FormField>,
)

/** Parse the `sections` array of a `schema` response. */
fun parseSections(data: JSONObject): List<FormSection> {
    val arr = data.optJSONArray("sections") ?: return emptyList()
    return (0 until arr.length()).map { i ->
        // Each entry is a 3-tuple [id, title, fields] — Python tuples arrive
        // as JSON arrays, not objects.
        val entry = arr.getJSONArray(i)
        val fields = entry.getJSONArray(2)
        FormSection(
            id = entry.getString(0),
            title = entry.getString(1),
            fields = (0 until fields.length()).map { j ->
                val f = fields.getJSONObject(j)
                val opts = f.optJSONArray("options")
                FormField(
                    path = f.getString("path"),
                    label = f.getString("label"),
                    kind = f.getString("kind"),
                    options = if (opts == null) emptyList()
                              else (0 until opts.length()).map { k -> opts.getString(k) },
                )
            },
        )
    }
}

/**
 * The flat `{path: value}` map a preset returns, as this form edits it.
 *
 * Booleans stay Boolean and everything else becomes a String, because the
 * Python coercion is asymmetric: it accepts a string for every numeric kind
 * (`float(value)`), but `bool("false")` is True — a boolean sent as text would
 * silently invert. Empty means blank rather than null: the loader treats "" and
 * None identically for optional fields, and "" avoids JSONObject.NULL.
 */
fun JSONObject.toFormValues(fields: List<FormField>): Map<String, Any> =
    fields.associate { f ->
        f.path to when {
            isNull(f.path) -> ""
            f.isBool -> optBoolean(f.path)
            else -> optString(f.path)
        }
    }

fun Map<String, Any>.toJson(): JSONObject {
    val o = JSONObject()
    forEach { (k, v) -> o.put(k, v) }
    return o
}
