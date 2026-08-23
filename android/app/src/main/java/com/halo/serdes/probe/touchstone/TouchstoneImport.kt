package com.halo.serdes.probe.touchstone

import android.content.Context
import android.net.Uri
import android.provider.OpenableColumns
import java.io.File

/**
 * Getting a user-picked Touchstone file onto a path Python can open.
 *
 * A SAF `Uri` is not a file. `content://…` is resolved by another app's
 * provider through the framework, and the Python side has nothing but
 * `open()` — so the bytes have to be copied into this app's own storage
 * first. That copy is not a workaround; it is the only thing that makes the
 * path meaningful to the interpreter.
 */
object TouchstoneImport {

    /** Where copies live. One directory, so [clear] can empty it. */
    private const val DIR = "imported"

    /**
     * Guard against a hand-slip on a multi-GB file.
     *
     * The largest channel file in this repo is 2.6 MB and it is the big one.
     * A Touchstone that exceeds this is far more likely to be the wrong file
     * than a legitimate one, and copying it would block on I/O with no way to
     * say what went wrong.
     */
    const val MAX_BYTES = 64L * 1024 * 1024

    class TooLarge(val bytes: Long) : Exception(
        "file is ${bytes / (1024 * 1024)} MB; the limit is " +
            "${MAX_BYTES / (1024 * 1024)} MB")

    /** MIME types worth offering the picker. */
    val MIME_TYPES = arrayOf("*/*")

    /**
     * Copy [uri] into private storage and return the file.
     *
     * Runs on a background dispatcher chosen by the caller — this does real
     * I/O through another process's content provider.
     */
    fun copyIn(context: Context, uri: Uri): File {
        val name = displayName(context, uri)
        val size = sizeOf(context, uri)
        if (size != null && size > MAX_BYTES) throw TooLarge(size)

        val dir = File(context.filesDir, DIR).apply { mkdirs() }
        val dest = File(dir, sanitise(name))
        context.contentResolver.openInputStream(uri)
            ?.use { input -> dest.outputStream().use { input.copyTo(it) } }
            ?: throw java.io.FileNotFoundException("cannot open $uri")
        if (dest.length() > MAX_BYTES) {
            // A provider may report no size up front; check after the fact so
            // the limit still means something.
            dest.delete()
            throw TooLarge(dest.length())
        }
        return dest
    }

    /**
     * Keep the extension. skrf reads the port count from `.sNp`, so a copy
     * saved as `import.dat` would fail to parse for a reason that has nothing
     * to do with its contents.
     */
    private fun sanitise(name: String): String {
        val cleaned = name.substringAfterLast('/').replace(Regex("[^A-Za-z0-9._-]"), "_")
        return if (cleaned.isBlank()) "imported.s4p" else cleaned
    }

    private fun displayName(context: Context, uri: Uri): String =
        query(context, uri, OpenableColumns.DISPLAY_NAME) { c, i -> c.getString(i) }
            ?: uri.lastPathSegment ?: "imported.s4p"

    private fun sizeOf(context: Context, uri: Uri): Long? =
        query(context, uri, OpenableColumns.SIZE) { c, i ->
            if (c.isNull(i)) null else c.getLong(i)
        }

    private fun <T> query(
        context: Context, uri: Uri, column: String, read: (android.database.Cursor, Int) -> T?,
    ): T? = runCatching {
        context.contentResolver.query(uri, arrayOf(column), null, null, null)
            ?.use { c ->
                val i = c.getColumnIndex(column)
                if (i >= 0 && c.moveToFirst()) read(c, i) else null
            }
    }.getOrNull()

    fun clear(context: Context) {
        File(context.filesDir, DIR).deleteRecursively()
    }
}
