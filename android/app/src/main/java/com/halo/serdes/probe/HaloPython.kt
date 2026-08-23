package com.halo.serdes.probe

import android.content.Context
import com.chaquo.python.PyObject
import com.chaquo.python.Python
import com.chaquo.python.android.AndroidPlatform

/**
 * The single place the app talks to Python.
 *
 * Two things matter here and nowhere else:
 *
 *  - `HALO_NO_JIT` must be set **before** the interpreter starts. numba has no
 *    Android wheels, and the framework's fourth architecture invariant is that
 *    the pure-Python kernels give identical results — so this is what makes the
 *    no-numba build legitimate rather than a silent compromise.
 *  - Python calls never run on the main thread. There is one interpreter and
 *    one GIL, so callers marshal onto a single background thread; the compute
 *    here takes ~0.4 s and a real time-domain run takes minutes.
 */
object HaloPython {

    @Volatile private var started = false

    @Synchronized
    fun start(context: Context) {
        if (started) return
        android.system.Os.setenv("HALO_NO_JIT", "1", true)
        if (!Python.isStarted()) {
            Python.start(AndroidPlatform(context.applicationContext))
        }
        started = true
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
     * The real facade, for when this spike grows into the app:
     * `call(method, payloadJson) -> json`. Strings both ways, because Chaquopy
     * marshals str<->String for free while dict<->Map needs PyObject walking.
     */
    fun call(context: Context, method: String, payloadJson: String = "{}"): String {
        start(context)
        return Python.getInstance()
            .getModule("halo_serdes_app.api")
            .callAttr("call", method, payloadJson)
            .toString()
    }
}
