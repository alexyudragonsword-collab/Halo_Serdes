package com.halo.serdes.probe

import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import com.halo.serdes.probe.ui.HaloTheme
import com.halo.serdes.probe.ui.HaloApp

/**
 * The whole app: two tabs, drawn by Compose, fed by the shared Python core.
 *
 * The M0 probe is no longer wired to a button — its job (does the embedded
 * stack load and compute the same numbers as the desktop?) is answered, and
 * the answer is kept honest by `PythonStackTest`, which calls
 * [HaloPython.probeJson] directly. Keeping a probe button on the real screen
 * would just be a dead control.
 */
class MainActivity : ComponentActivity() {

    override fun onCreate(savedInstanceState: Bundle?) {
        // targetSdk 35 draws behind the system bars on Android 15 whether or
        // not this is called; calling it makes every API level lay out the
        // same way, so the API 34 emulator tests the layout an Android 15
        // phone shows. The Scaffold's bars take the system-bar insets; the
        // content takes the keyboard's (HaloApp).
        enableEdgeToEdge()
        super.onCreate(savedInstanceState)
        setContent { HaloTheme { HaloApp() } }
    }
}
