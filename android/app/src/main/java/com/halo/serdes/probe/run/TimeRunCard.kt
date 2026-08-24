package com.halo.serdes.probe.run

import android.Manifest
import android.content.Context
import android.content.pm.PackageManager
import android.os.Build
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.LinearProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.SegmentedButton
import androidx.compose.material3.SegmentedButtonDefaults
import androidx.compose.material3.SingleChoiceSegmentedButtonRow
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.core.content.ContextCompat
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.halo.serdes.probe.ui.TestTags
import com.halo.serdes.probe.ui.TimeSummary

/**
 * The time-domain engine: pick a quality tier, start it, watch it, stop it.
 *
 * Separate from the statistical Run button because the two are not variants of
 * one action. The statistical engine answers in under a second and is meant to
 * be pressed while dragging a value; this is minutes of compute that survives
 * leaving the app, and presenting them side by side as equals would invite
 * pressing the expensive one by reflex.
 */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun TimeRunCard(
    enabled: Boolean,
    summary: TimeSummary?,
    onStart: (Context, Quality) -> Unit,
    onClear: (Context) -> Unit,
) {
    val ctx = LocalContext.current
    val s by TimeRunController.state.collectAsStateWithLifecycle()
    var quality by rememberSaveable { mutableStateOf(Quality.FAST) }

    // Only so the run's own progress notification stays visible on API 33+.
    // Asked at the moment it becomes relevant rather than on first launch,
    // where a permission prompt has no context to justify it.
    val askNotify = rememberLauncherForActivityResult(
        ActivityResultContracts.RequestPermission()
    ) { /* denied just means no notification; the run itself is unaffected */ }

    Card(Modifier.fillMaxWidth()) {
        Column(Modifier.padding(12.dp)) {
            Text("Time-domain engine",
                 style = MaterialTheme.typography.titleSmall,
                 fontWeight = FontWeight.Bold)
            Text("Counts real errors on simulated bits. Minutes, not " +
                 "milliseconds — it keeps running if you leave the app.",
                 style = MaterialTheme.typography.labelSmall,
                 color = MaterialTheme.colorScheme.onSurfaceVariant)

            Spacer(Modifier.height(8.dp))
            SingleChoiceSegmentedButtonRow(Modifier.fillMaxWidth()) {
                Quality.entries.forEachIndexed { i, q ->
                    SegmentedButton(
                        selected = quality == q,
                        onClick = { quality = q },
                        enabled = !s.active,
                        shape = SegmentedButtonDefaults.itemShape(i, Quality.entries.size),
                    ) { Text(q.label) }
                }
            }
            Text("${"%,d".format(quality.symbols)} symbols  ·  est. ${quality.estimate}",
                 style = MaterialTheme.typography.labelSmall,
                 color = MaterialTheme.colorScheme.onSurfaceVariant)

            Spacer(Modifier.height(8.dp))
            if (s.active) {
                RunningRow(s, onCancel = { TimeRunController.cancel(ctx) })
            } else {
                Row(horizontalArrangement = Arrangement.spacedBy(12.dp),
                    verticalAlignment = Alignment.CenterVertically) {
                    Button(
                        onClick = {
                            if (Build.VERSION.SDK_INT >= 33 && !ctx.canNotify()) {
                                askNotify.launch(Manifest.permission.POST_NOTIFICATIONS)
                            }
                            onStart(ctx, quality)
                        },
                        enabled = enabled,
                    ) { Text("Run ${quality.label.lowercase()}") }
                    if (s.state == "done" || s.state == "cancelled" ||
                        s.state == "error") {
                        OutlinedButton(onClick = { onClear(ctx) }) { Text("Clear") }
                    }
                }
            }

            when (s.state) {
                "cancelled" -> Note("Stopped. Nothing was measured.")
                "error" -> Note(s.error ?: "run failed",
                                MaterialTheme.colorScheme.error)
                else -> {}
            }

            summary?.let { Spacer(Modifier.height(8.dp)); TimeSummaryBody(it) }
        }
    }
}

@Composable
private fun RunningRow(s: TimeRunState, onCancel: () -> Unit) = Column {
    Row(Modifier.fillMaxWidth(),
        horizontalArrangement = Arrangement.spacedBy(12.dp),
        verticalAlignment = Alignment.CenterVertically) {
        CircularProgressIndicator(Modifier.size(20.dp).testTag(TestTags.BUSY))
        Column(Modifier.weight(1f)) {
            Text(if (s.cancelPending) "stopping…" else s.stage.ifBlank { "running" },
                 style = MaterialTheme.typography.bodyMedium)
            Text("%.0f s elapsed".format(s.elapsedS),
                 style = MaterialTheme.typography.labelSmall,
                 color = MaterialTheme.colorScheme.onSurfaceVariant)
        }
        OutlinedButton(onClick = onCancel, enabled = !s.cancelPending) {
            Text(if (s.cancelPending) "Stopping" else "Cancel")
        }
    }
    // Indeterminate for the same reason the notification is: the receiver
    // kernel is one call, so a percentage would be invented.
    LinearProgressIndicator(
        Modifier.fillMaxWidth().padding(top = 8.dp).testTag(TestTags.BUSY))
    if (s.cancelPending) {
        Note("Cancellation lands at the next stage boundary; the receiver " +
             "kernel cannot be interrupted part-way.")
    }
}

@Composable
private fun TimeSummaryBody(t: TimeSummary) = Column {
    Text(if (t.berIsUpperBound) "BER < %.2e".format(t.berBound)
         else "BER %.3e".format(t.ber),
         style = MaterialTheme.typography.headlineSmall,
         fontFamily = FontFamily.Monospace)
    if (t.berIsUpperBound) {
        // The distinction the whole card exists to make. Zero errors is not a
        // BER of zero; it is a statement about what this many symbols can see.
        Note("No errors in ${"%,d".format(t.nChecked)} checked bits. The true " +
             "BER is somewhere below that — run a longer tier to bound it lower.")
    } else {
        Note("${t.nErrors} errors in ${"%,d".format(t.nChecked)} bits" +
             (if (t.nErrors < 100) "  ·  too few to compare runs on" else ""))
    }
    KV("SER", "%.3e".format(t.ser))
    KV("symbols measured", "%,d of %,d".format(t.nSymbols, t.nRequested))
    KV("slicer SNR", "%.1f dB".format(t.slicerSnrDb))
    KV("engine time", "%.1f s".format(t.elapsedS))
}

@Composable
private fun Note(text: String, color: androidx.compose.ui.graphics.Color =
                     MaterialTheme.colorScheme.onSurfaceVariant) =
    Text(text, style = MaterialTheme.typography.labelSmall, color = color,
         modifier = Modifier.padding(top = 4.dp))

@Composable
private fun KV(k: String, v: String) = Row(
    Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween,
) {
    Text(k, style = MaterialTheme.typography.bodyMedium)
    Text(v, style = MaterialTheme.typography.bodyMedium,
         fontFamily = FontFamily.Monospace)
}

private fun Context.canNotify(): Boolean =
    Build.VERSION.SDK_INT < 33 || ContextCompat.checkSelfPermission(
        this, Manifest.permission.POST_NOTIFICATIONS
    ) == PackageManager.PERMISSION_GRANTED
