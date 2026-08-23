package com.halo.serdes.probe.touchstone

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
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp

/**
 * What a Touchstone file turned out to contain, before it is adopted.
 *
 * Deliberately a confirmation step rather than a straight-to-config import.
 * The one number that decides whether the file answers the question being
 * asked is [fileFMaxGhz] against [nyquistGhz] — a measured `.s4p` routinely
 * stops well below the Nyquist it gets pointed at, and the channel model then
 * extrapolates without complaint.
 */
data class TouchstoneInfo(
    val path: String,
    val name: String,
    val fileFMaxGhz: Double,
    val nyquistGhz: Double,
    val ilDbAtNyquist: Double,
    val nFreq: Int,
    val extrapolated: Boolean,
)

@Composable
fun TouchstoneCard(
    info: TouchstoneInfo?,
    busy: Boolean,
    error: String?,
    onPick: (android.net.Uri) -> Unit,
    onAdopt: (TouchstoneInfo) -> Unit,
    onDiscard: () -> Unit,
) {
    val pick = rememberLauncherForActivityResult(
        ActivityResultContracts.OpenDocument()
    ) { uri -> uri?.let(onPick) }

    Card(Modifier.fillMaxWidth()) {
        Column(Modifier.padding(12.dp)) {
            Text("Channel file", style = MaterialTheme.typography.titleSmall,
                 fontWeight = FontWeight.Bold)
            Text("Import a Touchstone .s2p/.s4p and use it as this link's " +
                 "channel.",
                 style = MaterialTheme.typography.labelSmall,
                 color = MaterialTheme.colorScheme.onSurfaceVariant)

            Spacer(Modifier.height(8.dp))
            Row(horizontalArrangement = Arrangement.spacedBy(12.dp),
                verticalAlignment = Alignment.CenterVertically) {
                Button(onClick = { pick.launch(TouchstoneImport.MIME_TYPES) },
                       enabled = !busy) { Text("Choose file…") }
                if (busy) CircularProgressIndicator(Modifier.size(20.dp))
            }

            error?.let {
                Text(it, style = MaterialTheme.typography.bodySmall,
                     color = MaterialTheme.colorScheme.error,
                     modifier = Modifier.padding(top = 8.dp))
            }

            info?.let { i ->
                Spacer(Modifier.height(8.dp))
                Text(i.name.substringAfterLast('/'),
                     fontFamily = FontFamily.Monospace,
                     style = MaterialTheme.typography.bodyMedium)
                KV("file spans to", "%.1f GHz".format(i.fileFMaxGhz))
                KV("Nyquist of this config", "%.1f GHz".format(i.nyquistGhz))
                KV("insertion loss at Nyquist", "%.2f dB".format(i.ilDbAtNyquist))
                KV("frequency points", "${i.nFreq}")

                if (i.extrapolated) {
                    // The whole reason this is a confirmation step. The loss
                    // above is still a number, and it did not come from the
                    // file.
                    Text(
                        "This file stops below the Nyquist of the current " +
                            "config. The loss above is extrapolated, not " +
                            "measured — lower the symbol rate or use a file " +
                            "that reaches %.1f GHz.".format(i.nyquistGhz),
                        style = MaterialTheme.typography.labelSmall,
                        color = MaterialTheme.colorScheme.error,
                        modifier = Modifier.padding(top = 4.dp),
                    )
                }

                Spacer(Modifier.height(8.dp))
                Row(horizontalArrangement = Arrangement.spacedBy(12.dp)) {
                    Button(onClick = { onAdopt(i) }) { Text("Use this channel") }
                    OutlinedButton(onClick = onDiscard) { Text("Discard") }
                }
            }
        }
    }
}

@Composable
private fun KV(k: String, v: String) = Row(
    Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween,
) {
    Text(k, style = MaterialTheme.typography.bodyMedium)
    Text(v, style = MaterialTheme.typography.bodyMedium,
         fontFamily = FontFamily.Monospace)
}
