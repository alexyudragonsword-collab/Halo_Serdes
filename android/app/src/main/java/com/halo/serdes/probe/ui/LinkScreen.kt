package com.halo.serdes.probe.ui

import androidx.compose.animation.AnimatedVisibility
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.ExposedDropdownMenuBox
import androidx.compose.material3.ExposedDropdownMenuDefaults
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.MenuAnchorType
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.lifecycle.viewmodel.compose.viewModel
import com.halo.serdes.probe.api.ApiResult
import com.halo.serdes.probe.run.TimeRunCard
import com.halo.serdes.probe.touchstone.TouchstoneCard
import com.halo.serdes.probe.ui.charts.EyeHeatMap
import com.halo.serdes.probe.ui.charts.LogLineChart

/**
 * Pick a preset, edit any of its parameters, run the statistical engine, read
 * a BER.
 *
 * The parameter form is generated from `config_bridge.SECTIONS` — the same
 * spec the desktop Dash app renders — so a new config field appears here with
 * no Kotlin change. Nothing on this screen is computed locally: derived
 * quantities, the envelope banner and the field errors all come from `derive`,
 * and the result from `run_stat`.
 */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun LinkScreen(vm: LinkViewModel = viewModel()) {
    val s by vm.state.collectAsStateWithLifecycle()

    Column(
        Modifier
            .fillMaxSize()
            .verticalScroll(rememberScrollState())
            .padding(16.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        PresetPicker(s, onSelect = vm::select)

        s.channelIssue?.let { msg ->
            // Distinct from an error card: nothing is wrong with the
            // config, the data file simply is not on this device. Run is
            // disabled rather than left to fail.
            InfoCard(
                MaterialTheme.colorScheme.surfaceVariant,
                MaterialTheme.colorScheme.onSurfaceVariant,
                "CHANNEL DATA UNAVAILABLE",
                "$msg\n\nPick another preset, or import a Touchstone " +
                    "file below.",
            )
        }

        TouchstoneCard(
            info = s.touchstone,
            bundled = s.bundledChannels,
            busy = s.touchstoneBusy,
            error = s.touchstoneError,
            onPick = vm::importTouchstone,
            onUseBundled = vm::useBundledChannel,
            onAdopt = vm::adoptTouchstone,
            onDiscard = vm::discardTouchstone,
        )

        s.envelope?.let { env ->
            val (bg, fg) = envelopeColors(env.level)
            InfoCard(bg, fg, env.level.uppercase(), env.message)
        }

        if (s.derived.isNotEmpty()) {
            Card(Modifier.fillMaxWidth()) {
                Column(Modifier.padding(12.dp)) {
                    s.derived.forEach { (k, v) -> KeyValue(k, v) }
                }
            }
        }

        Row(
            Modifier.fillMaxWidth(),
            horizontalArrangement = Arrangement.spacedBy(12.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Button(onClick = vm::run, enabled = s.ready) {
                Text("Run statistical engine")
            }
            if (s.busy || s.validating) {
                CircularProgressIndicator(
                    Modifier.size(20.dp).testTag(TestTags.BUSY))
            }
        }

        s.error?.let { ErrorCard(it) }

        s.stat?.let { r -> ResultCard(r, s, onShowEye = vm::loadEye) }

        TimeRunCard(
            enabled = s.ready,
            summary = s.time,
            onStart = vm::startTimeRun,
            onClear = vm::clearTimeRun,
        )

        s.warnings.forEach {
            InfoCard(
                MaterialTheme.colorScheme.surfaceVariant,
                MaterialTheme.colorScheme.onSurfaceVariant,
                "WARNING", it,
            )
        }

        HorizontalDivider()
        Text(
            "Parameters",
            style = MaterialTheme.typography.titleMedium,
        )
        s.sections.forEach { section ->
            SectionCard(section, s, onChange = vm::setField)
        }

        // Where the presets came from. Invisible plumbing until it breaks,
        // and when it breaks (nothing bundled) this line is the diagnosis.
        Text(
            "${s.presets.size} presets · ${s.sections.sumOf { it.fields.size }} " +
                "fields · ${s.configsDir}",
            style = MaterialTheme.typography.labelSmall,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
        )
    }
}

/**
 * One collapsible group.
 *
 * Collapsed by default: 78 fields open at once is a wall, and the presets
 * already carry sensible values for all of them. A section holding an invalid
 * field says so in its header, so a fold can never hide the reason Run is
 * disabled.
 */
@Composable
private fun SectionCard(
    section: FormSection,
    s: LinkUiState,
    onChange: (String, Any) -> Unit,
) {
    var open by rememberSaveable(section.id) { mutableStateOf(false) }
    val bad = section.fields.count { s.fieldErrors.containsKey(it.path) }

    Card(Modifier.fillMaxWidth()) {
        Column(Modifier.padding(12.dp)) {
            Row(
                Modifier.fillMaxWidth().clickable { open = !open },
                horizontalArrangement = Arrangement.SpaceBetween,
                verticalAlignment = Alignment.CenterVertically,
            ) {
                Text(
                    "${if (open) "▾" else "▸"}  ${section.title}",
                    style = MaterialTheme.typography.titleSmall,
                )
                Text(
                    if (bad > 0) "$bad invalid" else "${section.fields.size}",
                    style = MaterialTheme.typography.labelMedium,
                    color = if (bad > 0) MaterialTheme.colorScheme.error
                            else MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }
            AnimatedVisibility(visible = open) {
                FormSectionBody(section, s.values, s.fieldErrors, onChange)
            }
        }
    }
}

@Composable
private fun ResultCard(
    r: StatSummary,
    s: LinkUiState,
    onShowEye: () -> Unit,
) = Card(Modifier.fillMaxWidth()) {
    Column(Modifier.padding(12.dp)) {
        Text(
            "BER %.3e".format(r.ber),
            style = MaterialTheme.typography.headlineMedium,
            fontFamily = FontFamily.Monospace,
        )
        if (r.ber == 0.0) {
            // Not a failed run: the comfortable presets really do resolve no
            // error probability at the best phase. Say so, or an exact zero
            // reads as "it didn't run".
            Text(
                "no error probability resolved at the best phase",
                style = MaterialTheme.typography.labelSmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
        }
        KeyValue("SER", "%.3e".format(r.ser))
        KeyValue("best phase", "${r.bestPhi}")
        KeyValue("engine time", "%.0f ms".format(r.elapsedS * 1e3))

        if (r.bathtubY.any { it > 0.0 }) {
            Spacer(Modifier.height(8.dp))
            LogLineChart(
                x = r.bathtubX,
                y = r.bathtubY,
                modifier = Modifier.testTag(TestTags.BATHTUB),
                yLabel = "BER vs sampling phase",
                xLabel = "phase [UI]",
            )
        }

        Spacer(Modifier.height(8.dp))
        when {
            s.eye != null -> {
                Text("Statistical eye  (log₁₀ density)",
                     style = MaterialTheme.typography.labelSmall,
                     color = MaterialTheme.colorScheme.onSurfaceVariant)
                EyeHeatMap(
                    key = r.handle,
                    z = s.eye.z,
                    zmin = s.eye.zmin,
                    zmax = s.eye.zmax,
                    floorValue = s.eye.floor,
                    modifier = Modifier.testTag(TestTags.EYE),
                )
                Text(
                    "%.1f … %.1f, blank = no probability resolved"
                        .format(s.eye.zmin, s.eye.zmax),
                    style = MaterialTheme.typography.labelSmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }
            s.eyeLoading -> CircularProgressIndicator(
                Modifier.size(20.dp).testTag(TestTags.BUSY))
            // Not fetched with the run: a reduced eye is still ~4k numbers and
            // most presses of Run are to read a BER.
            else -> TextButton(onClick = onShowEye) { Text("Show statistical eye") }
        }
    }
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun PresetPicker(s: LinkUiState, onSelect: (String) -> Unit) {
    var open by remember { mutableStateOf(false) }
    ExposedDropdownMenuBox(expanded = open, onExpandedChange = { open = !open }) {
        OutlinedTextField(
            value = s.selected,
            onValueChange = {},
            readOnly = true,
            label = { Text("Preset") },
            trailingIcon = { ExposedDropdownMenuDefaults.TrailingIcon(expanded = open) },
            modifier = Modifier
                .menuAnchor(MenuAnchorType.PrimaryNotEditable)
                .fillMaxWidth(),
        )
        ExposedDropdownMenu(expanded = open, onDismissRequest = { open = false }) {
            s.presets.forEach { name ->
                DropdownMenuItem(
                    text = { Text(name) },
                    onClick = { open = false; onSelect(name) },
                )
            }
        }
    }
}

@Composable
private fun ErrorCard(e: ApiResult.Err) = Card(
    Modifier.fillMaxWidth(),
    colors = CardDefaults.cardColors(
        containerColor = MaterialTheme.colorScheme.errorContainer,
        contentColor = MaterialTheme.colorScheme.onErrorContainer,
    ),
) {
    Column(Modifier.padding(12.dp)) {
        Text(e.kind, fontWeight = FontWeight.Bold)
        Text(e.message, fontFamily = FontFamily.Monospace,
             style = MaterialTheme.typography.bodySmall)
        e.field?.let { Text("field: $it", style = MaterialTheme.typography.labelSmall) }
    }
}

@Composable
private fun InfoCard(bg: Color, fg: Color, title: String, body: String) = Card(
    Modifier.fillMaxWidth(),
    colors = CardDefaults.cardColors(containerColor = bg, contentColor = fg),
) {
    Column(Modifier.padding(12.dp)) {
        Text(title, fontWeight = FontWeight.Bold,
             style = MaterialTheme.typography.labelMedium)
        Text(body, style = MaterialTheme.typography.bodySmall)
    }
}

@Composable
private fun KeyValue(k: String, v: String) = Row(
    Modifier.fillMaxWidth(),
    horizontalArrangement = Arrangement.SpaceBetween,
) {
    Text(k, style = MaterialTheme.typography.bodyMedium)
    Text(v, style = MaterialTheme.typography.bodyMedium, fontFamily = FontFamily.Monospace)
}
