package com.halo.serdes.probe.ui

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
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
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.MenuAnchorType
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Text
import androidx.compose.material3.TopAppBar
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.lifecycle.viewmodel.compose.viewModel
import com.halo.serdes.probe.api.ApiResult

/**
 * M4: pick a preset, run the statistical engine, read a BER off the screen.
 *
 * Everything shown is produced by the shared Python core through the JSON
 * facade — there is no number on this screen that the desktop build would
 * compute differently. Editing individual parameters (the auto-generated form
 * driven by `SECTIONS`) is the next milestone; for now the presets are the
 * whole input surface.
 */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun LinkScreen(vm: LinkViewModel = viewModel()) {
    val s by vm.state.collectAsStateWithLifecycle()

    Scaffold(topBar = { TopAppBar(title = { Text("Halo SerDes") }) }) { pad ->
        Column(
            Modifier
                .padding(pad)
                .fillMaxSize()
                .verticalScroll(rememberScrollState())
                .padding(16.dp),
            verticalArrangement = Arrangement.spacedBy(12.dp),
        ) {
            PresetPicker(s, onSelect = vm::select)

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
                if (s.busy) CircularProgressIndicator(Modifier.size(20.dp))
            }

            s.error?.let { ErrorCard(it) }

            s.stat?.let { r ->
                Card(Modifier.fillMaxWidth()) {
                    Column(Modifier.padding(12.dp)) {
                        Text(
                            "BER %.3e".format(r.ber),
                            style = MaterialTheme.typography.headlineMedium,
                            fontFamily = FontFamily.Monospace,
                        )
                        if (r.ber == 0.0) {
                            // Not a failed run: the comfortable presets really
                            // do resolve no error probability at the best
                            // phase. Say so, or an exact zero reads as "it
                            // didn't run".
                            Text(
                                "no error probability resolved at the best phase",
                                style = MaterialTheme.typography.labelSmall,
                                color = MaterialTheme.colorScheme.onSurfaceVariant,
                            )
                        }
                        KeyValue("SER", "%.3e".format(r.ser))
                        KeyValue("best phase", "${r.bestPhi}")
                        KeyValue("bathtub points", "${r.bathtubPoints}")
                        KeyValue("engine time", "%.0f ms".format(r.elapsedS * 1e3))
                    }
                }
            }

            s.warnings.forEach {
                InfoCard(
                    MaterialTheme.colorScheme.surfaceVariant,
                    MaterialTheme.colorScheme.onSurfaceVariant,
                    "WARNING", it,
                )
            }

            // Where the presets came from. Invisible plumbing until it breaks,
            // and when it breaks (nothing bundled) this line is the diagnosis.
            Text(
                "${s.presets.size} presets · ${s.configsDir}",
                style = MaterialTheme.typography.labelSmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
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
        Text(e.message, fontFamily = FontFamily.Monospace, style = MaterialTheme.typography.bodySmall)
        e.field?.let { Text("field: $it", style = MaterialTheme.typography.labelSmall) }
    }
}

@Composable
private fun InfoCard(
    bg: Color,
    fg: Color,
    title: String,
    body: String,
) = Card(
    Modifier.fillMaxWidth(),
    colors = CardDefaults.cardColors(containerColor = bg, contentColor = fg),
) {
    Column(Modifier.padding(12.dp)) {
        Text(title, fontWeight = FontWeight.Bold, style = MaterialTheme.typography.labelMedium)
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
