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
    /** What the field does to the link, in a sentence (`form_meta.HELP`). */
    val help: String = "",
    /** Shown before "Advanced" (`form_meta.COMMON`). */
    val common: Boolean = false,
) {
    /** Optional fields may be left blank; the loader maps that back to None. */
    val optional: Boolean get() = kind.startsWith("opt_")

    /** True where the value crosses the boundary as a JSON boolean, not a string. */
    val isBool: Boolean get() = kind == "bool"

    /** Drawn as a drop-down. `opt_enum` adds a blank entry the loader reads as None. */
    val isEnum: Boolean get() = kind == "enum" || kind == "opt_enum"
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
    "float", "int", "bool", "enum", "opt_enum", "str", "opt_str",
    "opt_float", "opt_int", "tuple_float", "opt_tuple_float",
)

data class FormSection(
    val id: String,
    val title: String,
    val fields: List<FormField>,
)

/**
 * A stage of the link (Link & run, Transmitter, Channel, ...), holding config
 * sections by id. The same chain the desktop draws on its left; from
 * `form_meta.STAGES` through the `schema` call.
 */
data class FormStage(
    val id: String,
    val title: String,
    val sections: List<String>,
)

/**
 * What the form hides for the values on screen: what the configuration does
 * not use (the ADC group on a mixed-signal receiver, the RLGC line on a
 * Touchstone channel, the optical fields on an electrical link).
 *
 * Evaluated on the Python side and returned by every `derive`, so the rules
 * have one evaluator for both clients; nothing here reads them.
 */
data class Hidden(
    val fields: Set<String> = emptySet(),
    val sections: Set<String> = emptySet(),
    val stages: Set<String> = emptySet(),
)

/** Parse the `stages` array of a `schema` response (empty from an older one). */
fun parseStages(data: JSONObject): List<FormStage> {
    val arr = data.optJSONArray("stages") ?: return emptyList()
    return (0 until arr.length()).map { i ->
        val o = arr.getJSONObject(i)
        val secs = o.getJSONArray("sections")
        FormStage(o.getString("id"), o.getString("title"),
                  (0 until secs.length()).map { secs.getString(it) })
    }
}

/** Parse the `hidden` object of a `derive` response. */
fun parseHidden(o: JSONObject?): Hidden {
    fun set(key: String): Set<String> {
        val a = o?.optJSONArray(key) ?: return emptySet()
        return (0 until a.length()).map { a.getString(it) }.toSet()
    }
    return Hidden(set("fields"), set("sections"), set("stages"))
}

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
                    help = f.optString("help"),
                    common = f.optBoolean("common", false),
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
