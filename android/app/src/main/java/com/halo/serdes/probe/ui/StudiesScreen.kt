package com.halo.serdes.probe.ui

import androidx.compose.animation.AnimatedVisibility
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import com.halo.serdes.probe.ui.charts.MultiLineChart
import com.halo.serdes.probe.ui.charts.Series

/**
 * Every sweep the library offers, rendered from what the facade advertises.
 *
 * There is no study name anywhere in this file. The list, the titles, the
 * blurbs and the panel specs all arrive from `schema`, so a study added to
 * `studies.py` shows up here — with the right axes — without a Kotlin change.
 * That is the same bargain the parameter form makes with `SECTIONS`, and it is
 * what keeps one calculation core behind two UIs.
 *
 * The sweeps run against the parameters set on the Link screen, so they are
 * not independent of it — the header says so rather than leaving someone to
 * wonder which config a curve belongs to.
 */
@Composable
fun StudiesScreen(state: LinkUiState, vm: LinkViewModel) {
    Column(
        Modifier
            .fillMaxSize()
            .verticalScroll(rememberScrollState())
            .padding(16.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        Text("Sweeps of the current config: ${state.selected}",
             style = MaterialTheme.typography.labelMedium,
             color = MaterialTheme.colorScheme.onSurfaceVariant)

        if (state.channelIssue != null) {
            Text("The channel data for this config is not available, so " +
                 "nothing here can run. Choose another preset on the Link tab.",
                 style = MaterialTheme.typography.bodySmall,
                 color = MaterialTheme.colorScheme.error)
        }

        state.studies.forEach { study ->
            StudyCard(
                study = study,
                result = state.studyResults[study.name],
                running = state.studyRunning == study.name,
                enabled = state.ready && state.studyRunning == null,
                // Only meaningful for the sweeps that need one; the card uses
                // it to say whether pressing Run reuses that work or starts a
                // fresh time-domain run inside this call.
                hasTimeRun = state.time != null,
                onRun = { vm.runStudy(study.name) },
            )
        }

        if (state.studies.isEmpty()) {
            Text("No studies advertised by this build.",
                 style = MaterialTheme.typography.bodySmall)
        }
    }
}

@Composable
private fun StudyCard(
    study: StudyMeta,
    result: StudyResult?,
    running: Boolean,
    enabled: Boolean,
    hasTimeRun: Boolean,
    onRun: () -> Unit,
) {
    var open by rememberSaveable(study.name) { mutableStateOf(false) }

    Card(Modifier.fillMaxWidth()) {
        Column(Modifier.padding(12.dp)) {
            Row(Modifier.fillMaxWidth().clickable { open = !open },
                horizontalArrangement = Arrangement.SpaceBetween,
                verticalAlignment = Alignment.CenterVertically) {
                Text("${if (open) "▾" else "▸"}  ${study.title}",
                     style = MaterialTheme.typography.titleSmall,
                     fontWeight = FontWeight.Bold)
                if (result != null) {
                    Text(if (result.note != null) "n/a" else "✓",
                         style = MaterialTheme.typography.labelMedium,
                         color = MaterialTheme.colorScheme.onSurfaceVariant)
                }
            }

            AnimatedVisibility(visible = open) {
                Column {
                    Text(study.blurb,
                         style = MaterialTheme.typography.labelSmall,
                         color = MaterialTheme.colorScheme.onSurfaceVariant,
                         modifier = Modifier.padding(vertical = 6.dp))

                    if (study.needsTime) {
                        Text(
                            if (hasTimeRun)
                                "Reuses the time-domain run from the Link tab."
                            else
                                "Needs a time-domain run. Without one this " +
                                    "starts a fresh one inside the call — " +
                                    "minutes, with no progress and no cancel. " +
                                    "Run one on the Link tab first.",
                            style = MaterialTheme.typography.labelSmall,
                            color = if (hasTimeRun)
                                MaterialTheme.colorScheme.onSurfaceVariant
                            else MaterialTheme.colorScheme.error,
                            modifier = Modifier.padding(bottom = 6.dp),
                        )
                    }

                    Row(horizontalArrangement = Arrangement.spacedBy(12.dp),
                        verticalAlignment = Alignment.CenterVertically) {
                        Button(onClick = onRun, enabled = enabled && !running) {
                            Text(if (result == null) "Run sweep" else "Re-run")
                        }
                        if (running) CircularProgressIndicator(
                            Modifier.size(20.dp).testTag(TestTags.BUSY))
                    }

                    result?.note?.let {
                        // The study declined this config — e.g. a reach sweep
                        // needs a length to vary, which a Touchstone channel
                        // has not got. Information, not a failure.
                        Text(it, style = MaterialTheme.typography.bodySmall,
                             color = MaterialTheme.colorScheme.onSurfaceVariant,
                             modifier = Modifier.padding(top = 8.dp))
                    }

                    result?.takeIf { it.note == null }?.let { r ->
                        r.plots.forEach { panel ->
                            Spacer(Modifier.height(8.dp))
                            Panel(panel, r)
                        }
                        if (r.plots.isEmpty()) {
                            // No spec means no defensible chart, so show the
                            // numbers rather than invent axes for them.
                            Spacer(Modifier.height(8.dp))
                            r.data.forEach { (k, v) ->
                                Text("$k: ${v.take(6).joinToString(", ") { fmtSmall(it) }}" +
                                     if (v.size > 6) " …" else "",
                                     style = MaterialTheme.typography.labelSmall,
                                     fontFamily = FontFamily.Monospace)
                            }
                        }
                    }
                }
            }
        }
    }
}

@Composable
private fun Panel(spec: PlotSpec, r: StudyResult) {
    val x = r.data[spec.x]
    val series = spec.y.mapNotNull { key -> r.data[key]?.let { Series(key, it) } }
    if (x == null || series.isEmpty()) {
        // The spec named a key the study did not return. Say which, rather
        // than drawing an empty box: it means the two drifted apart.
        Text("panel needs ${(listOf(spec.x) + spec.y).filterNot { it in r.data }}",
             style = MaterialTheme.typography.labelSmall,
             color = MaterialTheme.colorScheme.error)
        return
    }
    MultiLineChart(
        x = x, series = series,
        modifier = Modifier.testTag(TestTags.STUDY_PANEL),
        xLabel = spec.xLabel, yLabel = spec.yLabel,
        xLog = spec.xLog, yLog = spec.yLog,
    )
}

private fun fmtSmall(v: Double): String =
    if (!v.isFinite()) "—"
    else if (v != 0.0 && (kotlin.math.abs(v) < 0.01 || kotlin.math.abs(v) >= 1e4))
        "%.2e".format(v) else "%.3g".format(v)
