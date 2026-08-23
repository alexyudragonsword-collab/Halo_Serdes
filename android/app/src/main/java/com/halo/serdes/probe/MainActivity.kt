package com.halo.serdes.probe

import android.os.Bundle
import android.view.Gravity
import android.widget.Button
import android.widget.LinearLayout
import android.widget.ScrollView
import android.widget.TextView
import androidx.appcompat.app.AppCompatActivity
import java.util.concurrent.Executors

/**
 * Deliberately plain — no Compose, no Material 3, no navigation.
 *
 * This activity exists to answer one question ("does the embedded Python stack
 * work on this device?"), so everything that is not that question is left out.
 * Adding a UI framework here would only add version-matching risk to a spike
 * whose whole value is a fast, unambiguous yes/no.
 */
class MainActivity : AppCompatActivity() {

    private val py = Executors.newSingleThreadExecutor()   // one interpreter, one GIL

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)

        val out = TextView(this).apply {
            textSize = 12f
            setPadding(24, 24, 24, 24)
            typeface = android.graphics.Typeface.MONOSPACE
            text = "Tap Run to start the embedded interpreter."
        }
        val run = Button(this).apply { text = "Run probe" }
        val root = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            gravity = Gravity.TOP
            addView(run)
            addView(ScrollView(this@MainActivity).apply { addView(out) })
        }
        setContentView(root)

        run.setOnClickListener {
            run.isEnabled = false
            out.text = "starting interpreter + importing numpy/scipy…"
            py.execute {
                val text = try {
                    HaloPython.probeSummary(this)
                } catch (t: Throwable) {
                    "FAILED\n${t::class.java.simpleName}: ${t.message}"
                }
                runOnUiThread {
                    out.text = text
                    run.isEnabled = true
                }
            }
        }
    }

    override fun onDestroy() {
        py.shutdown()
        super.onDestroy()
    }
}
