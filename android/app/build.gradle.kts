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

    // The Python source is the repository's own package tree — NOT a copy.
    // Keeping one source of truth is the whole point: the app and the desktop
    // build must never diverge (project invariant #1).
    sourceSets["main"].python {
        srcDirs("src/main/python", "../../src")
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
}

chaquopy {
    defaultConfig {
        version = "${properties["pythonVersion"]}"

        pip {
            // Exactly what src/halo_serdes needs and no more. matplotlib is
            // never imported by the library (only by examples/), numba has no
            // Android wheels and is optional by design, and galois is only
            // needed for real FEC encode/decode — the projection formulas use
            // scipy.stats alone.
            install("numpy")
            install("scipy")     // <-- the wheel whose existence M0 tests
            install("scikit-rf") // pure Python; enables Touchstone import
            install("PyYAML")
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
