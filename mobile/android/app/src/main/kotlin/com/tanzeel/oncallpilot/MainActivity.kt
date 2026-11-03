package com.tanzeel.oncallpilot

import android.app.NotificationChannel
import android.app.NotificationManager
import android.os.Build
import android.os.Bundle
import io.flutter.embedding.android.FlutterFragmentActivity

// local_auth needs a FragmentActivity (architecture §10.5, H14).
class MainActivity : FlutterFragmentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        createIncidentChannel()
    }

    // The channel every push uses (C9 §3, MOB-016). Creating it again is a no-op.
    private fun createIncidentChannel() {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.O) return
        val channel = NotificationChannel(
            "incidents_critical",
            "Incidents",
            NotificationManager.IMPORTANCE_HIGH,
        ).apply { description = "New incidents, approvals, failures and resolutions" }
        getSystemService(NotificationManager::class.java).createNotificationChannel(channel)
    }
}
