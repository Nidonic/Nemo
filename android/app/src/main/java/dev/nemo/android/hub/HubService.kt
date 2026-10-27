package dev.nemo.android.hub

import android.app.PendingIntent
import android.app.Service
import android.content.Context
import android.content.Intent
import android.content.pm.ServiceInfo
import android.os.IBinder
import androidx.core.app.NotificationCompat
import androidx.core.app.ServiceCompat
import dev.nemo.android.MainActivity
import dev.nemo.android.NemoApp
import dev.nemo.android.R

/** Keeps the phone on the hub while the app is in the background, so other devices can reach it. */
class HubService : Service() {
    override fun onBind(intent: Intent?): IBinder? = null

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        val open = PendingIntent.getActivity(this, 0, Intent(this, MainActivity::class.java), PendingIntent.FLAG_IMMUTABLE)
        val n = NotificationCompat.Builder(this, NemoApp.CHANNEL_HUB)
            .setSmallIcon(R.mipmap.ic_launcher)
            .setContentTitle("Nemo is reachable")
            .setContentText("Your other devices can hand this phone tasks.")
            .setContentIntent(open)
            .setOngoing(true)
            .build()
        ServiceCompat.startForeground(this, 7, n, ServiceInfo.FOREGROUND_SERVICE_TYPE_SPECIAL_USE)
        (application as NemoApp).graph.hub.start()
        return START_STICKY
    }

    override fun onDestroy() {
        (application as NemoApp).graph.hub.stop()
        super.onDestroy()
    }

    companion object {
        fun start(context: Context) = context.startForegroundService(Intent(context, HubService::class.java))
        fun stop(context: Context) = context.stopService(Intent(context, HubService::class.java))
    }
}
