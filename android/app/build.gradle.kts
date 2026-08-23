plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
    id("com.chaquo.python")
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
        testInstrumentationRunner = "androidx.test.runner.AndroidJUnitRunner"

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

// configs/ only (36 KB). data/channels/*.s4p is deliberately left out: reading
// Touchstone needs scikit-rf, which M0 does not install, so the 4.4 MB would
// buy nothing this milestone can use.
val stageHaloAssets by tasks.registering(Copy::class) {
    from(rootProject.file("../configs")) { into("halo_data/configs") }
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
        setSrcDirs(listOf("src/main/python", "../../src"))
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
            // scikit-rf is deliberately NOT here: it declares pandas as a hard
            // dependency (even though it never imports it at import time), so
            // it risks failing the build on a package M0 has no opinion about.
            // Touchstone import belongs to a later milestone; adding it now
            // would only blur the one question this spike asks.
            install("numpy")
            install("scipy")     // <-- the wheel whose existence M0 tests
            install("PyYAML")    // presets are YAML; loader imports it lazily
        }

        // Keep .py sources so a traceback on the device names real lines.
        pyc { src = false }
    }
}

dependencies {
    implementation("androidx.appcompat:appcompat:1.7.0")
    androidTestImplementation("androidx.test.ext:junit:1.2.1")
    androidTestImplementation("androidx.test:runner:1.6.2")
}
