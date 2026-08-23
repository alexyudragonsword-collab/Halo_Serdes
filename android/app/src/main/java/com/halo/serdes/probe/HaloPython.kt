package com.halo.serdes.probe

import android.content.Context
import com.chaquo.python.PyObject
import com.chaquo.python.Python
import com.chaquo.python.android.AndroidPlatform
import kotlinx.coroutines.CoroutineDispatcher
import kotlinx.coroutines.asCoroutineDispatcher
import kotlinx.coroutines.withContext
import java.io.File
import java.util.concurrent.Executors

/**
 * The single place the app talks to Python.
 *
 * Three things matter here and nowhere else:
 *
 *  - `HALO_NO_JIT` must be set **before** the interpreter starts. numba has no
 *    Android wheels, and the framework's fourth architecture invariant is that
 *    the pure-Python kernels give identical results — so this is what makes the
 *    no-numba build legitimate rather than a silent compromise.
 *  - `HALO_SERDES_DATA_DIR` must likewise be set before the interpreter starts,
 *    because `config_bridge` resolves `configs/` once at import time. Chaquopy
 *    serves Python modules from an archive, so the library's `__file__`-relative
 *    search finds nothing on a device; the presets have to come from assets
 *    extracted to real storage.
 *  - Python calls never run on the main thread. There is one interpreter and
 *    one GIL, so callers marshal onto a single background thread; the compute
 *    here takes ~0.4 s and a real time-domain run takes minutes.
 */
object HaloPython {

    /** Must match `DATA_DIR_ENV` in `halo_serdes_app/config_bridge.py`. */
    private const val DATA_DIR_ENV = "HALO_SERDES_DATA_DIR"

    /** Asset subtree staged by the `stageHaloAssets` Gradle task. */
    private const val ASSET_ROOT = "halo_data"

    /**
     * The one thread every Python call runs on.
     *
     * Not [kotlinx.coroutines.Dispatchers.IO]: there is a single interpreter
     * behind a single GIL, so concurrency here would buy nothing and would let
     * two calls interleave inside the registry that [HaloApi] hands out
     * handles from. One thread also means Python-side state (imported modules,
     * stored results) has one owner.
     */
    private val executor =
        Executors.newSingleThreadExecutor { r -> Thread(r, "halo-python") }

    val dispatcher: CoroutineDispatcher = executor.asCoroutineDispatcher()

    /** Run [block] on the interpreter thread, starting Python if needed. */
    suspend fun <T> on(context: Context, block: () -> T): T =
        withContext(dispatcher) {
            start(context)
            block()
        }

    /**
     * Queue [block] on the interpreter thread and return immediately.
     *
     * For cleanup that outlives the scope that would normally await it — most
     * of all releasing a stored result in `onCleared`, where `viewModelScope`
     * is already cancelled and a coroutine would simply never run.
     */
    fun post(block: () -> Unit) = executor.execute(block)

    @Volatile private var started = false

    @Synchronized
    fun start(context: Context) {
        if (started) return
        val app = context.applicationContext
        android.system.Os.setenv("HALO_NO_JIT", "1", true)
        android.system.Os.setenv(DATA_DIR_ENV, extractData(app).absolutePath, true)
        if (!Python.isStarted()) {
            Python.start(AndroidPlatform(app))
        }
        started = true
    }

    /**
     * Copy the bundled data assets into private storage and return the root.
     *
     * Unconditional rather than first-run-only: the payload is ~36 KB of YAML,
     * so re-copying costs nothing measurable, while a "already extracted" flag
     * would happily serve last version's presets after an upgrade.
     */
    private fun extractData(context: Context): File {
        val dest = File(context.filesDir, ASSET_ROOT)
        copyAssetTree(context, ASSET_ROOT, dest)
        return dest
    }

    private fun copyAssetTree(context: Context, assetPath: String, dest: File) {
        val am = context.assets
        // list() returns empty for a file and non-empty for a directory; there
        // is no isDirectory in the AssetManager API.
        val children = am.list(assetPath) ?: emptyArray()
        if (children.isEmpty()) {
            dest.parentFile?.mkdirs()
            am.open(assetPath).use { input ->
                dest.outputStream().use { input.copyTo(it) }
            }
            return
        }
        dest.mkdirs()
        for (child in children) {
            copyAssetTree(context, "$assetPath/$child", File(dest, child))
        }
    }

    private fun probe(): PyObject = Python.getInstance().getModule("halo_probe")

    /** Full JSON report — versions, scipy entry points, compute, golden diff. */
    fun probeJson(context: Context): String {
        start(context)
        return probe().callAttr("run").toString()
    }

    /** A few lines fit for a phone screen. */
    fun probeSummary(context: Context): String {
        start(context)
        return probe().callAttr("summary").toString()
    }

    /**
     * The facade: `call(method, payloadJson) -> json`. Strings both ways,
     * because Chaquopy marshals str<->String for free while dict<->Map needs
     * PyObject walking.
     *
     * Blocking, and it starts the interpreter — fine from a test or from
     * [on], but app code should go through
     * [com.halo.serdes.probe.api.HaloApi] instead, which cannot be called off
     * the interpreter thread and returns parsed results rather than raw JSON.
     */
    fun call(context: Context, method: String, payloadJson: String = "{}"): String {
        start(context)
        return callOnThisThread(method, payloadJson)
    }

    /** As [call], but assumes the caller is already on [dispatcher] and started. */
    internal fun callOnThisThread(method: String, payloadJson: String): String =
        Python.getInstance()
            .getModule("halo_serdes_app.api")
            .callAttr("call", method, payloadJson)
            .toString()
}
