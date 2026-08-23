package com.halo.serdes.probe

import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import com.halo.serdes.probe.api.ApiResult
import com.halo.serdes.probe.api.HaloApi
import com.halo.serdes.probe.ui.RENDERABLE_KINDS
import com.halo.serdes.probe.ui.parseSections
import com.halo.serdes.probe.ui.toFormValues
import com.halo.serdes.probe.ui.toJson
import kotlinx.coroutines.runBlocking
import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test
import org.junit.runner.RunWith

/**
 * The generated parameter form's contract with the config schema.
 *
 * The form is not hand-written: it is produced from `config_bridge.SECTIONS`,
 * so what it can render has to keep up with what the schema declares. These
 * tests fail when the two drift apart, rather than letting a new field appear
 * on screen as an unlabelled or unrenderable box.
 */
@RunWith(AndroidJUnit4::class)
class FormContractTest {

    private val ctx get() = InstrumentationRegistry.getInstrumentation().targetContext

    private suspend fun schema(): JSONObject =
        (HaloApi.schema(ctx) as ApiResult.Ok).data

    @Test
    fun everyDeclaredFieldKindHasAWidget() = runBlocking {
        val sections = parseSections(schema())
        assertTrue("no sections in schema", sections.isNotEmpty())

        val kinds = sections.flatMap { it.fields }.map { it.kind }.toSet()
        val unknown = kinds - RENDERABLE_KINDS
        assertTrue("schema declares kinds the form cannot draw: $unknown", unknown.isEmpty())

        // Labels carry the unit; paths are wire keys and are never shown.
        sections.flatMap { it.fields }.forEach {
            assertTrue("field ${it.path} has no label", it.label.isNotBlank())
        }
        sections.flatMap { it.fields }.filter { it.kind == "enum" }.forEach {
            assertTrue("enum ${it.path} has no options", it.options.isNotEmpty())
        }
    }

    /**
     * A preset survives the trip into the form's value map and back.
     *
     * The form edits everything as text, so this is where a lost or
     * type-mangled field would show up — the map must still validate.
     */
    @Test
    fun presetRoundTripsThroughTheFormValueMap() = runBlocking {
        val sections = parseSections(schema())
        val fields = sections.flatMap { it.fields }
        val name = schema().getJSONArray("presets").getString(1)   // first real preset

        val raw = (HaloApi.preset(ctx, name) as ApiResult.Ok).data.getJSONObject("values")
        val values = raw.toFormValues(fields)
        assertEquals("form dropped fields", fields.size, values.size)

        val d = HaloApi.derive(ctx, values.toJson())
        assertTrue("round-tripped preset failed to derive: $d", d is ApiResult.Ok)
        assertTrue((d as ApiResult.Ok).data.getBoolean("valid"))
    }

    /** Editing a field must actually move the derived quantities. */
    @Test
    fun editingSymbolRateChangesTheDerivedUi() = runBlocking {
        val fields = parseSections(schema()).flatMap { it.fields }
        val name = schema().getJSONArray("presets").getString(1)
        val values = (HaloApi.preset(ctx, name) as ApiResult.Ok)
            .data.getJSONObject("values").toFormValues(fields)

        fun uiOf(v: Map<String, Any>) = runBlocking {
            ((HaloApi.derive(ctx, v.toJson())) as ApiResult.Ok)
                .data.getJSONObject("derived").getString("UI")
        }

        val before = uiOf(values)
        // As the form sends it: a string, not a number.
        val after = uiOf(values + ("symbol_rate" to "14"))
        assertTrue("UI did not change: $before -> $after", before != after)
        assertTrue("UI lost its unit: $after", after.endsWith("ps"))
    }

    /** An invalid entry comes back keyed by its own path, so the box can go red. */
    @Test
    fun aBadFieldIsReportedAgainstItsOwnPath() = runBlocking {
        val fields = parseSections(schema()).flatMap { it.fields }
        val name = schema().getJSONArray("presets").getString(1)
        val values = (HaloApi.preset(ctx, name) as ApiResult.Ok)
            .data.getJSONObject("values").toFormValues(fields)

        val d = (HaloApi.derive(ctx, (values + ("osr" to "0")).toJson()) as ApiResult.Ok).data
        assertTrue("expected invalid", !d.getBoolean("valid"))
        val errs = d.getJSONObject("field_errors")
        assertTrue("error not keyed by path: ${errs}", errs.has("osr"))
    }

    /**
     * Booleans must cross as JSON booleans.
     *
     * The Python coercion is asymmetric: every numeric kind accepts a string,
     * but `bool("false")` is True — a switch sent as text would silently invert
     * and the config would be wrong with nothing to show for it. `toFormValues`
     * is what keeps them typed, so pin that it does.
     */
    @Test
    fun boolFieldsStayBooleanThroughTheValueMap() = runBlocking {
        val fields = parseSections(schema()).flatMap { it.fields }
        val bools = fields.filter { it.isBool }
        assertTrue("schema has no bool fields to check", bools.isNotEmpty())

        val name = schema().getJSONArray("presets").getString(1)
        val values = (HaloApi.preset(ctx, name) as ApiResult.Ok)
            .data.getJSONObject("values").toFormValues(fields)

        bools.forEach {
            assertTrue("${it.path} is ${values[it.path]?.javaClass}, not Boolean",
                       values[it.path] is Boolean)
        }
        val json = (values + (bools[0].path to false)).toJson()
        assertEquals(false, json.get(bools[0].path))
    }
}
