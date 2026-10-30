package app.devoirdujour

import android.app.Activity
import android.app.AlertDialog
import android.os.Bundle
import android.view.View
import android.widget.Button
import android.widget.EditText
import android.widget.TextView
import android.widget.Toast
import java.security.MessageDigest
import java.time.LocalDate
import java.time.ZoneId

/** Écran parent protégé par code : déblocage manuel, débogage USB, désinstallation. */
class AdminActivity : Activity() {
    private lateinit var prefs: Prefs

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_admin)
        prefs = Prefs(this)

        val pin = requireViewById<EditText>(R.id.pin)
        requireViewById<Button>(R.id.pin_ok).setOnClickListener { checkPin(pin.text.toString()) }

        requireViewById<Button>(R.id.unlock_hour).setOnClickListener {
            unlockUntil(System.currentTimeMillis() + 60 * 60 * 1000L)
        }
        requireViewById<Button>(R.id.unlock_day).setOnClickListener {
            unlockUntil(LocalDate.now().plusDays(1).atStartOfDay(ZoneId.systemDefault()).toInstant().toEpochMilli())
        }
        requireViewById<Button>(R.id.relock).setOnClickListener {
            prefs.adminUnlockUntil = 0L
            LockManager.refresh(this, launch = false)
            toast("Déblocage manuel annulé")
            finish()
        }
        requireViewById<Button>(R.id.toggle_adb).setOnClickListener {
            prefs.adbAllowed = !prefs.adbAllowed
            LockManager.applyPolicies(this)
            showAdmin()
        }
        requireViewById<Button>(R.id.remove_owner).setOnClickListener { confirmRemoveOwner() }
    }

    private fun checkPin(value: String) {
        val now = System.currentTimeMillis()
        if (now < prefs.pinLockedUntil) {
            toast("Trop d'essais, réessayez plus tard")
            return
        }
        if (sha256(value) == BuildConfig.ADMIN_PIN_SHA256) {
            prefs.failedPinAttempts = 0
            showAdmin()
        } else {
            prefs.failedPinAttempts += 1
            if (prefs.failedPinAttempts >= 5) {
                prefs.failedPinAttempts = 0
                prefs.pinLockedUntil = now + 15 * 60 * 1000L
            }
            toast("Code incorrect")
        }
    }

    private fun showAdmin() {
        requireViewById<View>(R.id.pin_group).visibility = View.GONE
        requireViewById<View>(R.id.admin_group).visibility = View.VISIBLE
        requireViewById<TextView>(R.id.info).text = buildString {
            append("Propriétaire de l'appareil : ")
            append(if (LockManager.isDeviceOwner(this@AdminActivity)) "oui" else "NON (blocage inactif)")
            append("\nServeur : ").append(BuildConfig.SERVER_URL)
            append("\nApps autorisées pendant le blocage : ")
            append(LockManager.allowedPackages(this@AdminActivity).sorted().joinToString(", "))
        }
        requireViewById<Button>(R.id.toggle_adb).text =
            if (prefs.adbAllowed) "Interdire le débogage USB (recommandé)" else "Autoriser le débogage USB"
    }

    private fun unlockUntil(until: Long) {
        prefs.adminUnlockUntil = until
        LockManager.refresh(this, launch = false)
        toast("Téléphone débloqué")
        finish()
    }

    private fun confirmRemoveOwner() {
        AlertDialog.Builder(this)
            .setTitle("Désactiver le contrôle ?")
            .setMessage(
                "Toutes les restrictions seront levées et l'application pourra être désinstallée. " +
                    "Pour la réactiver, il faudra refaire l'installation par adb."
            )
            .setPositiveButton("Désactiver") { _, _ ->
                try {
                    stopLockTask()
                } catch (e: Exception) {
                    // pas en mode kiosque
                }
                LockManager.removeDeviceOwner(this)
                toast("Contrôle désactivé")
                finish()
            }
            .setNegativeButton("Annuler", null)
            .show()
    }

    private fun toast(text: String) = Toast.makeText(this, text, Toast.LENGTH_LONG).show()

    private fun sha256(value: String): String =
        MessageDigest.getInstance("SHA-256").digest(value.toByteArray()).joinToString("") { "%02x".format(it) }
}
