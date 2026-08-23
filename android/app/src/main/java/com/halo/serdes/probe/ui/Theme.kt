package com.halo.serdes.probe.ui

import androidx.compose.foundation.isSystemInDarkTheme
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.darkColorScheme
import androidx.compose.material3.lightColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.ui.graphics.Color

// Fixed schemes rather than Material You dynamic colour: this is an instrument,
// and a warning banner that turns amber on one phone and lilac on another is
// worse than one that always looks the same.
private val Dark = darkColorScheme(
    primary = Color(0xFF7FD1FF),
    onPrimary = Color(0xFF00344A),
    surfaceVariant = Color(0xFF23282D),
)

private val Light = lightColorScheme(
    primary = Color(0xFF00658F),
    onPrimary = Color.White,
    surfaceVariant = Color(0xFFE3E7EB),
)

@Composable
fun HaloTheme(content: @Composable () -> Unit) =
    MaterialTheme(colorScheme = if (isSystemInDarkTheme()) Dark else Light, content = content)

/** Banner colours for the envelope levels the config layer reports. */
@Composable
fun envelopeColors(level: String): Pair<Color, Color> = when (level) {
    "crit" -> MaterialTheme.colorScheme.errorContainer to
        MaterialTheme.colorScheme.onErrorContainer
    "warn" -> Color(0xFFFFE08A) to Color(0xFF3B2E00)
    else -> MaterialTheme.colorScheme.surfaceVariant to
        MaterialTheme.colorScheme.onSurfaceVariant
}
