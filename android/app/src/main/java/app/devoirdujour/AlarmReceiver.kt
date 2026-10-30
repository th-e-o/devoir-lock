package app.devoirdujour

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent

/** Réveil périodique (toutes les 15 min et à minuit) : synchronise avec le serveur puis rebloque si besoin. */
class AlarmReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        val pending = goAsync()
        Thread {
            try {
                try {
                    LockManager.applyServerState(context, Api.today())
                } catch (e: Exception) {
                    // Hors ligne : on se fie au jeton déjà stocké (valable seulement aujourd'hui).
                }
                LockManager.refresh(context, launch = true)
            } finally {
                pending.finish()
            }
        }.start()
    }
}
