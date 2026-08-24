package com.halo.serdes.probe

import android.graphics.Bitmap
import androidx.compose.ui.graphics.ImageBitmap
import androidx.compose.ui.graphics.asAndroidBitmap
import androidx.compose.ui.graphics.toArgb
import androidx.compose.ui.graphics.toPixelMap
import androidx.compose.ui.test.assertIsDisplayed
import androidx.compose.ui.test.captureToImage
import androidx.compose.ui.test.junit4.createAndroidComposeRule
import androidx.compose.ui.test.onAllNodesWithTag
import androidx.compose.ui.test.onAllNodesWithText
import androidx.compose.ui.test.onFirst
import androidx.compose.ui.test.onNodeWithTag
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.onRoot
import androidx.compose.ui.test.performClick
import androidx.compose.ui.test.performScrollTo
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import com.halo.serdes.probe.ui.TestTags
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith
import java.io.File

/**
 * The screen, on a device, with the real Python behind it.
 *
 * Everything else in this suite talks to the facade directly. Nothing before
 * this launched the Activity at all, so "does the chart actually draw?" and
 * "did the bundled channels reach the screen?" were answerable only by
 * installing the APK and looking — which is a check that quietly stops
 * happening.
 *
 * Two kinds of assertion here, and they are not the same strength:
 *
 *  - **Structure.** Nodes exist, matched by their visible text where they have
 *    any. Matching text rather than a tag puts the label under test too.
 *  - **Pixels.** A Canvas exposes no text, so a chart that renders blank is
 *    indistinguishable from a link with nothing to show. [assertDrew] reads
 *    the captured bitmap and requires real variation in it. It deliberately
 *    does *not* compare against a golden image: those break on every emulator
 *    font and GPU change, and the re-baselining habit that follows turns them
 *    into no check at all.
 *
 * Screenshots are written out as evidence for a human, not as an oracle. The
 * assertions are what fails the build.
 */
@RunWith(AndroidJUnit4::class)
class UiRenderTest {

    @get:Rule
    val compose = createAndroidComposeRule<MainActivity>()

    private val ctx get() = InstrumentationRegistry.getInstrumentation().targetContext

    /**
     * Where screenshots land: internal storage, pulled with `run-as`.
     *
     * Not `getExternalFilesDir`. Four screenshots were written there and none
     * came back — `adb pull` of `/sdcard/Android/data/<pkg>` goes through the
     * scoped-storage FUSE layer on API 30+ and does not reliably work, so the
     * files existed on the device and the artifact was empty. `run-as` reaches
     * a debuggable app's own directory directly and has no such layer in the
     * way.
     */
    private val shots: File by lazy {
        File(ctx.filesDir, "screenshots").apply { mkdirs() }
    }

    /**
     * Wait for a condition rather than for the tree to go idle.
     *
     * Compose's automatic sync waits for idleness, and this app is never idle
     * on the terms that check uses: every call into Python runs on a
     * single-thread dispatcher that is not an IdlingResource, and a spinner is
     * an indefinite animation. So the startup path — load the schema, then
     * probe presets until one whose channel data is present — has to be waited
     * out explicitly.
     */
    private fun awaitText(text: String, timeoutMs: Long = 120_000) {
        compose.waitUntil(timeoutMs) {
            compose.onAllNodesWithText(text, substring = true)
                .fetchSemanticsNodes().isNotEmpty()
        }
        awaitSettled(timeoutMs)
    }

    /**
     * Wait until no spinner is on screen.
     *
     * Every interaction below — `assertIsDisplayed`, `performClick`,
     * `performScrollTo` — syncs with the composition first, and that sync
     * waits for the clock to be idle. An indeterminate progress indicator is
     * an endless animation, so the clock never goes idle while one is up and
     * the call blocks until the job times out rather than failing with
     * anything you could read.
     *
     * `waitUntil` polls its condition instead of demanding idleness, which is
     * why the gate can be written at all. Every such indicator carries
     * `TestTags.BUSY` so this can ask about all of them at once.
     */
    private fun awaitSettled(timeoutMs: Long = 120_000) =
        compose.waitUntil(timeoutMs) {
            compose.onAllNodesWithTag(TestTags.BUSY).fetchSemanticsNodes().isEmpty()
        }

    private fun awaitTag(tag: String, timeoutMs: Long = 120_000) {
        compose.waitUntil(timeoutMs) {
            compose.onAllNodesWithTag(tag).fetchSemanticsNodes().isNotEmpty()
        }
        awaitSettled(timeoutMs)
    }

    private fun seesText(text: String) =
        compose.onAllNodesWithText(text, substring = true)
            .fetchSemanticsNodes().isNotEmpty()

    private fun shoot(name: String) {
        val png = File(shots, "$name.png")
        png.outputStream().use { out ->
            compose.onRoot().captureToImage().asAndroidBitmap()
                .compress(Bitmap.CompressFormat.PNG, 100, out)
        }
    }

    /**
     * Require that a captured region contains actual drawing.
     *
     * A blank Canvas is one flat colour. Counting distinct colours is robust
     * across devices in a way a golden image is not: it says "something was
     * painted here" without caring which pixel. Sampled on a stride and
     * stopped as soon as the bar is cleared, so a large capture costs little.
     */
    private fun assertDrew(img: ImageBitmap, label: String, minColors: Int = 12) {
        val px = img.toPixelMap()
        val seen = HashSet<Int>()
        var y = 0
        while (y < px.height && seen.size <= minColors) {
            var x = 0
            while (x < px.width && seen.size <= minColors) {
                // toArgb(), NOT value.toInt(). Color is an inline class over a
                // ULong that packs ARGB into the *top* 32 bits and the colour
                // space id into the bottom ones, so `value.toInt()` keeps the
                // half that is zero for every sRGB colour — every pixel hashes
                // to the same number and the check reports "1 distinct colour"
                // for any image whatsoever. It did, on a bathtub that had
                // drawn perfectly well.
                seen.add(px[x, y].toArgb())
                x += 2
            }
            y += 2
        }
        assertTrue("$label drew only ${seen.size} distinct colours — blank?",
                   seen.size > minColors)
    }

    // ------------------------------------------------------------------ //

    @Test
    fun theLinkScreenOpensOnAPresetItCanActuallyRun() {
        awaitText("Run statistical engine")
        // Not just "a preset is selected": the one the ViewModel picked must be
        // the first *runnable* one. Opening on a preset whose Run is disabled
        // reads as a broken app, which is why firstRunnable exists.
        val expected = TestPresets.firstRunnableName(ctx)
        assertTrue("opened on something other than $expected", seesText(expected))
        compose.onNodeWithText("Run statistical engine").assertIsDisplayed()
        compose.onNodeWithText("Parameters").performScrollTo().assertIsDisplayed()
        shoot("01-link")
    }

    @Test
    fun bundledChannelsReachTheScreenAsChips() {
        // The gap this closed: three .s4p ship in the APK, two are named by no
        // preset, and the system document picker cannot see private storage.
        // These chips are the only way in, so their absence is a regression no
        // facade test would notice.
        awaitText("Channel file")
        compose.onNodeWithText("Channel file").performScrollTo()
        awaitTag(TestTags.BUNDLED_CHANNELS)
        compose.onNodeWithTag(TestTags.BUNDLED_CHANNELS).assertIsDisplayed()
        val labels = TestPresets.bundledChannelLabels(ctx)
        assertTrue("facade listed no bundled channels", labels.isNotEmpty())
        for (name in labels) {
            assertTrue("no chip for $name", seesText(name))
        }
        shoot("02-channels")
    }

    @Test
    fun theTimeDomainCardOffersAllThreeQualityTiers() {
        awaitText("Time-domain engine")
        compose.onNodeWithText("Time-domain engine").performScrollTo()
        for (tier in listOf("Fast", "Standard", "Precise")) {
            assertTrue("no $tier tier on screen", seesText(tier))
        }
        // The estimate is what stops someone starting a ten-minute run
        // expecting seconds, so it has to be on the screen, not only in the
        // enum that defines it.
        assertTrue("quality tier shows no time estimate", seesText("est."))
        shoot("03-timerun")
    }

    @Test
    fun runningTheStatisticalEngineDrawsABathtub() {
        awaitText("Run statistical engine")
        compose.onNodeWithText("Run statistical engine").performClick()

        // ~0.4 s of engine on ARM; longer on a loaded emulator. awaitText also
        // waits out the spinner the click just raised, which is what makes the
        // assertions after it safe to run.
        awaitText("BER ", timeoutMs = 180_000)

        awaitTag(TestTags.BATHTUB)
        compose.onNodeWithTag(TestTags.BATHTUB).performScrollTo()
        assertDrew(compose.onNodeWithTag(TestTags.BATHTUB).captureToImage(),
                   "bathtub")
        shoot("04-result")

        // The eye is fetched on demand — reduced it is still ~4k numbers, and
        // most presses of Run are to read a BER.
        compose.onNodeWithText("Show statistical eye").performScrollTo().performClick()
        // Fetching the eye raises a spinner of its own.
        awaitTag(TestTags.EYE, timeoutMs = 180_000)
        compose.onNodeWithTag(TestTags.EYE).performScrollTo()
        assertDrew(compose.onNodeWithTag(TestTags.EYE).captureToImage(), "eye")
        shoot("05-eye")
    }

    @Test
    fun theSweepsTabListsWhatTheFacadeAdvertises() {
        awaitText("Run statistical engine")
        compose.onAllNodesWithText("Sweeps", substring = true).onFirst().performClick()
        awaitText("Sweeps of the current config")
        awaitSettled()

        // No study name is hardcoded on this side, so this test does not name
        // one either — it asks the facade what should be there.
        val titles = TestPresets.studyTitles(ctx)
        assertTrue("facade advertised no studies", titles.isNotEmpty())
        for (t in titles) {
            assertTrue("no card for study '$t'", seesText(t))
        }
        shoot("06-sweeps")
    }
}
