package com.halo.serdes.probe.ui

import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.consumeWindowInsets
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.imePadding
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.NavigationBar
import androidx.compose.material3.NavigationBarItem
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Text
import androidx.compose.material3.TopAppBar
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.testTag
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.lifecycle.viewmodel.compose.viewModel

/**
 * The app shell: two tabs over one [LinkViewModel].
 *
 * One ViewModel on purpose. The sweeps on the Studies tab are sweeps *of the
 * config being edited on the Link tab* — giving them separate state would mean
 * two sources of truth for one configuration, which is the first project
 * invariant read backwards. `viewModel()` resolves against the activity here,
 * so both tabs address the same instance.
 */
// ExperimentalLayoutApi: consumeWindowInsets(PaddingValues), experimental in
// some Compose releases; opting in costs nothing where it is not
@OptIn(ExperimentalMaterial3Api::class, ExperimentalLayoutApi::class)
@Composable
fun HaloApp(vm: LinkViewModel = viewModel()) {
    val state by vm.state.collectAsStateWithLifecycle()
    var tab by rememberSaveable { mutableStateOf(0) }

    Scaffold(
        topBar = {
            TopAppBar(title = { Text("Halo SerDes") }, modifier = Modifier.testTag(TestTags.TOP_BAR))
        },
        bottomBar = {
            NavigationBar(Modifier.testTag(TestTags.NAV_BAR)) {
                NavigationBarItem(
                    selected = tab == 0,
                    onClick = { tab = 0 },
                    icon = { Text("⎓") },
                    label = { Text("Link") },
                )
                NavigationBarItem(
                    selected = tab == 1,
                    onClick = { tab = 1 },
                    icon = { Text("∿") },
                    // The count is the one thing worth showing here: it comes
                    // from the facade, so a zero means the build advertised
                    // none rather than the tab being broken.
                    label = { Text("Sweeps (${state.studies.size})") },
                )
            }
        },
    ) { pad ->
        // Edge to edge, the keyboard no longer shrinks the window: it arrives
        // as an inset. imePadding gives the scrolling form that room so the
        // focused field can scroll above it; consumeWindowInsets first, so
        // the part of the keyboard the bottom bar already covers is not
        // padded twice.
        Column(Modifier.padding(pad).consumeWindowInsets(pad).imePadding().fillMaxSize()) {
            when (tab) {
                0 -> LinkScreen(vm)
                else -> StudiesScreen(state, vm)
            }
        }
    }
}
