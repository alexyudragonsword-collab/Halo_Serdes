package com.halo.serdes.probe.run

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.Context
import android.content.Intent
import android.content.pm.ServiceInfo
import android.os.Build
import android.os.IBinder
import com.halo.serdes.probe.MainActivity
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.flow.collectLatest
import kotlinx.coroutines.launch

/**
 * Keeps the process alive while a time-domain run is in flight.
 *
 * The run is a Python thread inside this process. Without a foreground
 * service, backgrounding the app makes that process eligible for death and the
 * work vanishes with no trace — a minute of compute silently thrown away. That
 * is the failure this milestone exists to prevent; the notification is the
 * price Android charges for preventing it, and it doubles as the only progress
 * indicator visible once the screen is gone.
 *
 * It deliberately owns no state: [TimeRunController] runs the job and this
 * mirrors it into a notification.
 */
class TimeRunService : Service() {

    private val scope = CoroutineScope(SupervisorJob())

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        if (intent?.action == ACTION_CANCEL) {
            TimeRunController.cancel(this)
            return START_NOT_STICKY
        }
        createChannel()
        val first = buildNotification("starting", 0.0, false)
        if (Build.VERSION.SDK_INT >= 34) {
            startForeground(NOTIFICATION_ID, first,
                            ServiceInfo.FOREGROUND_SERVICE_TYPE_SPECIAL_USE)
        } else {
            startForeground(NOTIFICATION_ID, first)
        }

        scope.launch {
            TimeRunController.state.collectLatest { s ->
                if (!s.active) return@collectLatest
                notificationManager.notify(
                    NOTIFICATION_ID,
                    buildNotification(s.stage, s.elapsedS, s.cancelPending),
                )
            }
        }
        // NOT sticky: the run is a Python thread in this process, so if the
        // process dies the work is gone. A restarted service would advertise
        // progress on a job that no longer exists.
        return START_NOT_STICKY
    }

    override fun onDestroy() {
        scope.coroutineContext[kotlinx.coroutines.Job]?.cancel()
        super.onDestroy()
    }

    private val notificationManager
        get() = getSystemService(NotificationManager::class.java)

    private fun createChannel() {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.O) return
        notificationManager.createNotificationChannel(
            NotificationChannel(CHANNEL_ID, "Simulation runs",
                                NotificationManager.IMPORTANCE_LOW)
        )
    }

    private fun buildNotification(
        stage: String, elapsedS: Double, cancelPending: Boolean,
    ): Notification {
        val open = PendingIntent.getActivity(
            this, 0, Intent(this, MainActivity::class.java),
            PendingIntent.FLAG_IMMUTABLE)
        val cancel = PendingIntent.getService(
            this, 1, Intent(this, TimeRunService::class.java).setAction(ACTION_CANCEL),
            PendingIntent.FLAG_IMMUTABLE)

        val text = buildString {
            append(if (cancelPending) "stopping after this stage" else stage)
            if (elapsedS > 0) append("  ·  %.0f s".format(elapsedS))
        }
        return Notification.Builder(this, CHANNEL_ID)
            .setContentTitle("Time-domain run")
            .setContentText(text)
            .setSmallIcon(android.R.drawable.stat_notify_sync)
            .setOngoing(true)
            // Indeterminate on purpose: the receiver kernel is a single call
            // that dominates the run, so any percentage would be invented.
            .setProgress(0, 0, true)
            .setContentIntent(open)
            .addAction(Notification.Action.Builder(
                null, if (cancelPending) "Stopping…" else "Cancel", cancel).build())
            .build()
    }

    companion object {
        private const val CHANNEL_ID = "halo.run"
        private const val NOTIFICATION_ID = 1
        private const val ACTION_CANCEL = "com.halo.serdes.probe.CANCEL_RUN"

        fun start(context: Context) {
            val app = context.applicationContext
            app.startForegroundService(Intent(app, TimeRunService::class.java))
        }

        fun stop(context: Context) {
            val app = context.applicationContext
            app.stopService(Intent(app, TimeRunService::class.java))
        }
    }
}
