// M0 feasibility spike — see android/README.md.
//
// VERSIONS ARE THE POINT OF THIS SPIKE, not an incidental detail: whether a
// SciPy wheel exists for the chosen Python is what decides the whole Android
// route (chaquo/chaquopy#1237). Bump `chaquopyVersion` / `pythonVersion` in
// gradle.properties and let CI tell you — a missing wheel fails the build with
// a clear pip error, which is exactly the answer M0 is after.
//
// Versions are resolved here rather than in build.gradle.kts because a
// `plugins { id(...) version "..." }` block only accepts constants — gradle
// properties are readable at this stage, not that one.

pluginManagement {
    repositories {
        gradlePluginPortal()
        google()
        mavenCentral()
        maven("https://chaquo.com/maven")   // Chaquopy plugin + its runtime
    }
    val agpVersion: String by settings
    val kotlinVersion: String by settings
    val chaquopyVersion: String by settings
    plugins {
        id("com.android.application") version agpVersion
        id("org.jetbrains.kotlin.android") version kotlinVersion
        id("com.chaquo.python") version chaquopyVersion
    }
}

dependencyResolutionManagement {
    repositoriesMode.set(RepositoriesMode.FAIL_ON_PROJECT_REPOS)
    repositories {
        google()
        mavenCentral()
        maven("https://chaquo.com/maven")
    }
}

rootProject.name = "HaloSerdesProbe"
include(":app")
