package com.halo.serdes.probe

import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import com.halo.serdes.probe.ui.HaloTheme
import com.halo.serdes.probe.ui.LinkScreen

/**
 * The whole app: one screen, drawn by Compose, fed by the shared Python core.
 *
 * The M0 probe is no longer wired to a button — its job (does the embedded
 * stack load and compute the same numbers as the desktop?) is answered, and
 * the answer is kept honest by `PythonStackTest`, which calls
 * [HaloPython.probeJson] directly. Keeping a probe button on the real screen
 * would just be a dead control.
 */
class MainActivity : ComponentActivity() {

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContent { HaloTheme { LinkScreen() } }
    }
}
