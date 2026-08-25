plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
    id("org.jetbrains.kotlin.plugin.compose")
    id("com.chaquo.python")
}

// --- interpreted vs compiled ------------------------------------------------
//
// Two builds come out of this one file, and which one you get is decided by
// whether android/tools/build_compiled_wheels.sh has put wheels in pysrc/.
// Nothing else moves: same manifest, same Kotlin, same Compose, same tasks.
//
//   (nothing in pysrc/)  Chaquopy takes the repository's own src/ as a Python
//                        source dir. Desktop and phone run the same files,
//                        which is what ironclad rule #1 asks for.
//
//   (wheels in pysrc/)   The same packages arrive as .so inside a wheel, and
//                        ../../src is dropped from srcDirs -- with both in
//                        place the interpreted tree would shadow the compiled
//                        wheel and the APK would be the interpreted one under
//                        a different name. That failure is invisible in the
//                        build log, which is why CI gates both artifacts with
//                        android/tools/inspect_apk.py --native / --pure.
val pysrc = file("pysrc")
val compiledWheels = pysrc.listFiles { f: File -> f.name.matches(Regex("""halo_serdes-.*\.whl""")) }
    ?.sorted() ?: emptyList()
val compiledVariant = compiledWheels.isNotEmpty()
if (compiledVariant) {
    logger.lifecycle("Chaquopy: compiled variant, ${compiledWheels.size} wheel(s) in ${pysrc.path}")
    compiledWheels.forEach { logger.lifecycle("  ${it.name}") }
} else {
    logger.lifecycle("Chaquopy: interpreted variant (no wheels in ${pysrc.path})")
}

android {
    namespace = "com.halo.serdes.probe"
    compileSdk = 35

    defaultConfig {
        applicationId = "com.halo.serdes.probe"
        minSdk = 26
        targetSdk = 35
        versionCode = 1
        versionName = "0.0.1-m0"
        // So an APK on disk says which variant it is. The dangerous failure
        // here is two builds that differ in filename only; this at least makes
        // `aapt dump badging` able to tell them apart, and the inspect_apk
        // gate in CI is what actually enforces the difference.
        versionNameSuffix = if (compiledVariant) "-compiled" else null
        testInstrumentationRunner = "androidx.test.runner.AndroidJUnitRunner"
        // Files a test writes must leave the device *during* the run. AGP
        // uninstalls both APKs when connectedAndroidTest finishes, taking the
        // app's storage with them — so screenshots written to filesDir or to
        // the external files dir were already gone by the time the script
        // tried to pull them, and every retrieval route was doomed regardless
        // of where they had been put.
        testInstrumentationRunnerArguments["useTestStorageService"] = "true"

        ndk {
            // arm64-v8a is the real target; x86_64 is here only so the CI
            // emulator (which is x86_64) can run the instrumented test. Drop
            // x86_64 for a device-only build to halve the APK.
            abiFilters += listOf("arm64-v8a", "x86_64")
        }
    }

    buildTypes {
        release {
            isMinifyEnabled = false          // Chaquopy uses reflection
            signingConfig = signingConfigs.getByName("debug")   // sideload only
        }
    }

    buildFeatures { compose = true }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    kotlinOptions { jvmTarget = "17" }

    // The repo's configs/ ride along as Android assets, staged by the task
    // below. They cannot go through Chaquopy's python srcDirs: that importer
    // serves modules out of an archive, so a plain Path() lookup for a
    // non-Python data file never resolves. HaloPython extracts these to the
    // app's private storage and points $HALO_SERDES_DATA_DIR at them.
    sourceSets.getByName("main") {
        assets.srcDir(layout.buildDirectory.dir("generated/haloAssets"))
    }
}

// configs/ (36 KB) and, since M8 installs scikit-rf, data/channels/*.s4p
// (4.4 MB) as well — without a reader those files were dead weight, and with
// one they are the difference between five runnable presets and nine.
val stageHaloAssets by tasks.registering(Copy::class) {
    from(rootProject.file("../configs")) { into("halo_data/configs") }
    from(rootProject.file("../data/channels")) { into("halo_data/data/channels") }
    into(layout.buildDirectory.dir("generated/haloAssets"))
}

tasks.withType<com.android.build.gradle.tasks.MergeSourceSetFolders>()
    .configureEach { dependsOn(stageHaloAssets) }

chaquopy {
    // Python source dirs belong to the `chaquopy` block, NOT to android's
    // sourceSets — `sourceSets["main"].python { }` does not compile under the
    // Kotlin DSL (the `python` extension is registered dynamically), which is
    // what the first CI run reported. Kotlin DSL also needs setSrcDirs(list),
    // and because it *replaces* the default, "src/main/python" is listed
    // explicitly alongside the repo tree.
    //
    // "../../src" resolves from android/app to the repository's own src/, so
    // the Python is the real package tree and not a copy — desktop and phone
    // must never diverge (project invariant #1).
    sourceSets.getByName("main") {
        // The compiled variant must NOT list ../../src: Chaquopy would then
        // ship the .py alongside the wheel's .so and Python would import
        // whichever it found first. See the note at the top of this file.
        setSrcDirs(
            if (compiledVariant) listOf("src/main/python")
            else listOf("src/main/python", "../../src")
        )
    }

    defaultConfig {
        // providers.gradleProperty is unambiguous here; plain `properties[...]`
        // is what failed inside the root plugins block.
        version = providers.gradleProperty("pythonVersion").get()

        pip {
            // Only what M0 needs to give an answer. matplotlib is never
            // imported by the library (only by examples/), numba has no
            // Android wheels and is optional by design, and galois is only
            // needed for real FEC encode/decode — the projection formulas use
            // scipy.stats alone.
            //
            install("numpy")
            install("scipy")     // <-- the wheel whose existence M0 tests
            install("PyYAML")    // presets are YAML; loader imports it lazily

            // Touchstone import (M8). scikit-rf declares pandas as a hard
            // dependency it never reaches for on this path — verified on a
            // host by blocking the import outright and running the whole read
            // the engine runs, which is what test_import_hygiene.py now pins.
            //
            // Installed anyway, deps and all. `install("--no-deps", "...")`
            // is not a form Chaquopy accepts ("Invalid pip install format"),
            // and its `options()` applies to *every* install — which would
            // strip numpy and scipy of chaquopy-openblas and friends, i.e.
            // break the thing this whole port rests on to save a package.
            // Paying for pandas is the smaller price.
            install("scikit-rf")

            if (compiledVariant) {
                // --find-links, never install-by-path. pip matches a wheel to
                // the build by its *tag*, and it only does that when it is
                // choosing from a link set; given a path it installs whatever
                // it was handed, so the arm64 wheel lands in the x86_64 APK
                // too and fails at import with an ELF-header error that points
                // nowhere near here.
                //
                // --find-links is additive and scoped. --no-index would not be:
                // it is a global pip option and numpy/scipy/scikit-rf above are
                // resolved from Chaquopy's own index in this same pass.
                options("--find-links", pysrc.absolutePath)
                install("halo-serdes")
            }
        }

        // Keep .py sources so a traceback on the device names real lines.
        pyc { src = false }
    }
}

dependencies {
    // The BOM pins every compose-* artifact to one tested set, so individual
    // versions are never stated and cannot drift apart.
    val composeBom = platform("androidx.compose:compose-bom:2024.10.01")
    implementation(composeBom)
    implementation("androidx.compose.material3:material3")
    implementation("androidx.compose.ui:ui")
    implementation("androidx.compose.ui:ui-tooling-preview")
    debugImplementation("androidx.compose.ui:ui-tooling")

    // Direct dependency: HaloPython owns the single-thread interpreter
    // dispatcher, so this is not just transitive plumbing from lifecycle.
    implementation("org.jetbrains.kotlinx:kotlinx-coroutines-android:1.8.1")

    // ContextCompat.checkSelfPermission for POST_NOTIFICATIONS.
    implementation("androidx.core:core-ktx:1.13.1")
    implementation("androidx.activity:activity-compose:1.9.3")
    implementation("androidx.lifecycle:lifecycle-viewmodel-compose:2.8.7")
    implementation("androidx.lifecycle:lifecycle-runtime-compose:2.8.7")

    androidTestImplementation("androidx.test.ext:junit:1.2.1")
    androidTestImplementation("androidx.test:runner:1.6.2")

    // Compose UI tests. Until these, nothing in CI ever launched the Activity
    // — the screen's appearance was verified only by installing the APK and
    // looking, which is not a check that survives anyone forgetting to look.
    // The BOM again, explicitly: androidTestImplementation does not extend
    // implementation, so without this line ui-test-junit4 resolves to no
    // version at all and the build fails. (debugImplementation below does
    // extend it, which is why the manifest artifact needs no such line.)
    androidTestImplementation(composeBom)
    androidTestImplementation("androidx.compose.ui:ui-test-junit4")
    // TestStorage: the supported way for an instrumented test to emit files.
    // AGP copies them off the device while the app is still installed, into
    // build/outputs/connected_android_test_additional_output/.
    androidTestImplementation("androidx.test.services:storage:1.5.0")
    androidTestUtil("androidx.test.services:test-services:1.5.0")
    // NOT ui-test-manifest. That exists to supply an empty Activity for
    // createComposeRule(); these tests use createAndroidComposeRule<MainActivity>
    // and launch the real one, so it would be a dependency carried on a
    // justification that does not apply.
}
