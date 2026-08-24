package com.halo.serdes.probe.ui

/**
 * Semantics tags the UI tests address nodes by.
 *
 * Only for nodes a test cannot reach any other way. Buttons and headings are
 * matched by their visible text instead — a test that finds "Run statistical
 * engine" is also checking the label still says that, while a tag would let
 * the wording rot silently.
 *
 * What genuinely needs a tag is a Canvas: it draws pixels and exposes no text,
 * so without one there is no way to ask whether the chart drew anything. That
 * is the failure this whole layer exists to catch — a chart that renders blank
 * looks identical to a link with nothing to show.
 */
object TestTags {
    const val BATHTUB = "chart.bathtub"
    const val EYE = "chart.eye"
    const val STUDY_PANEL = "chart.study"
    const val BUNDLED_CHANNELS = "row.bundledChannels"

    /**
     * Every indeterminate progress indicator in the app.
     *
     * Not for finding them — for knowing when none is on screen. An
     * indeterminate indicator is an endless animation, and Compose's automatic
     * test synchronisation waits for the clock to go idle, so a UI test that
     * touches a node while a spinner is up blocks forever rather than failing.
     * A shared tag lets a test wait for "nothing is spinning" before doing
     * anything that syncs.
     */
    const val BUSY = "progress.busy"
}
