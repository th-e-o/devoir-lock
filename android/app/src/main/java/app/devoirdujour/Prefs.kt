package app.devoirdujour

import android.content.Context

/** État local persistant (survit aux redémarrages ; l'effacement des données est bloqué pour un Device Owner). */
class Prefs(context: Context) {
    private val sp = context.getSharedPreferences("devoir", Context.MODE_PRIVATE)

    /** Dernier jeton de déblocage reçu du serveur (valable uniquement pour sa date). */
    var unlockToken: String?
        get() = sp.getString("unlock_token", null)
        set(value) = sp.edit().putString("unlock_token", value).apply()

    /** Dernier état JSON renvoyé par le serveur, pour l'affichage hors ligne. */
    var cachedState: String?
        get() = sp.getString("cached_state", null)
        set(value) = sp.edit().putString("cached_state", value).apply()

    /** Déblocage manuel par le parent (code PIN), jusqu'à cet instant (ms epoch). */
    var adminUnlockUntil: Long
        get() = sp.getLong("admin_unlock_until", 0L)
        set(value) = sp.edit().putLong("admin_unlock_until", value).apply()

    /** Lanceur d'origine du téléphone (ComponentName aplati), utilisé quand le téléphone est débloqué. */
    var originalLauncher: String?
        get() = sp.getString("original_launcher", null)
        set(value) = sp.edit().putString("original_launcher", value).apply()

    /** Débogage USB autorisé (utile pendant l'installation, à couper ensuite). */
    var adbAllowed: Boolean
        get() = sp.getBoolean("adb_allowed", true)
        set(value) = sp.edit().putBoolean("adb_allowed", value).apply()

    var failedPinAttempts: Int
        get() = sp.getInt("failed_pin", 0)
        set(value) = sp.edit().putInt("failed_pin", value).apply()

    var pinLockedUntil: Long
        get() = sp.getLong("pin_locked_until", 0L)
        set(value) = sp.edit().putLong("pin_locked_until", value).apply()

    /** Pages photographiées mais pas encore envoyées (chemins absolus). */
    var pendingPages: List<String>
        get() = sp.getString("pending_pages", "")!!.split("\n").filter { it.isNotEmpty() }
        set(value) = sp.edit().putString("pending_pages", value.joinToString("\n")).apply()
}
