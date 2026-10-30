package app.devoirdujour

import android.app.admin.DevicePolicyManager
import android.content.ComponentName
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.content.pm.ApplicationInfo
import android.content.pm.PackageManager
import android.os.Build
import android.os.UserManager
import android.provider.MediaStore
import android.telecom.TelecomManager
import org.json.JSONObject

/**
 * Politique de verrouillage. Nécessite que l'app soit « Device Owner »
 * (adb shell dpm set-device-owner app.devoirdujour/.AdminReceiver).
 *
 * Bloqué  : notre écran est le lanceur (Home) imposé et l'app tourne en mode kiosque (lock task),
 *           seuls le téléphone, l'appareil photo et les paquets autorisés peuvent s'ouvrir.
 * Débloqué : le lanceur d'origine redevient le Home imposé, le mode kiosque est levé.
 */
object LockManager {
    private val RESTRICTIONS = listOf(
        UserManager.DISALLOW_SAFE_BOOT,
        UserManager.DISALLOW_FACTORY_RESET,
        UserManager.DISALLOW_ADD_USER,
        UserManager.DISALLOW_CONFIG_DATE_TIME,
    )

    fun admin(context: Context) = ComponentName(context, AdminReceiver::class.java)

    private fun dpm(context: Context): DevicePolicyManager =
        context.getSystemService(DevicePolicyManager::class.java)!!

    fun isDeviceOwner(context: Context): Boolean = dpm(context).isDeviceOwnerApp(context.packageName)

    fun isUnlocked(context: Context): Boolean {
        val prefs = Prefs(context)
        return UnlockToken.isValidForToday(prefs.unlockToken) ||
            System.currentTimeMillis() < prefs.adminUnlockUntil
    }

    /** Le serveur fait foi : s'il ne renvoie pas de jeton (ex. validation annulée), on oublie l'ancien. */
    fun applyServerState(context: Context, state: JSONObject) {
        val prefs = Prefs(context)
        prefs.unlockToken = state.optString("unlock_token").ifEmpty { null }
        prefs.cachedState = state.toString()
    }

    fun allowedPackages(context: Context): Set<String> {
        val pm = context.packageManager
        val packages = mutableSetOf(context.packageName)
        context.getSystemService(TelecomManager::class.java)?.defaultDialerPackage?.let(packages::add)
        pm.queryIntentActivities(Intent(MediaStore.ACTION_IMAGE_CAPTURE), 0)
            .forEach { packages.add(it.activityInfo.packageName) }
        BuildConfig.EXTRA_ALLOWED_PACKAGES.split(",").map { it.trim() }.filter { it.isNotEmpty() }
            .forEach(packages::add)
        return packages
    }

    fun applyPolicies(context: Context) {
        if (!isDeviceOwner(context)) return
        val dpm = dpm(context)
        val admin = admin(context)
        RESTRICTIONS.forEach { dpm.addUserRestriction(admin, it) }
        if (Prefs(context).adbAllowed) {
            dpm.clearUserRestriction(admin, UserManager.DISALLOW_DEBUGGING_FEATURES)
        } else {
            dpm.addUserRestriction(admin, UserManager.DISALLOW_DEBUGGING_FEATURES)
        }
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.R) {
            dpm.setAutoTimeEnabled(admin, true)
        } else {
            @Suppress("DEPRECATION")
            dpm.setAutoTimeRequired(admin, true)
        }
        dpm.setLockTaskPackages(admin, allowedPackages(context).toTypedArray())
        dpm.setLockTaskFeatures(
            admin,
            DevicePolicyManager.LOCK_TASK_FEATURE_GLOBAL_ACTIONS or // menu marche/arrêt, appel d'urgence
                DevicePolicyManager.LOCK_TASK_FEATURE_KEYGUARD or
                DevicePolicyManager.LOCK_TASK_FEATURE_SYSTEM_INFO,
        )
    }

    /** Mémorise le lanceur d'origine (une seule fois). */
    fun rememberOriginalLauncher(context: Context) {
        val prefs = Prefs(context)
        if (prefs.originalLauncher != null) return
        val home = Intent(Intent.ACTION_MAIN).addCategory(Intent.CATEGORY_HOME)
        val candidates = context.packageManager
            .queryIntentActivities(home, PackageManager.MATCH_ALL)
            .map { it.activityInfo }
            .filter { it.packageName != context.packageName && it.packageName != "com.android.settings" }
        val launcher = candidates.firstOrNull { it.applicationInfo.flags and ApplicationInfo.FLAG_SYSTEM != 0 }
            ?: candidates.firstOrNull()
        launcher?.let { prefs.originalLauncher = ComponentName(it.packageName, it.name).flattenToString() }
    }

    fun originalLauncher(context: Context): ComponentName? =
        Prefs(context).originalLauncher?.let(ComponentName::unflattenFromString)

    /** Impose notre écran (bloqué) ou le lanceur d'origine (débloqué) comme écran d'accueil. */
    fun setHome(context: Context, locked: Boolean) {
        if (!isDeviceOwner(context)) return
        val dpm = dpm(context)
        val admin = admin(context)
        val launcher = originalLauncher(context)
        dpm.clearPackagePersistentPreferredActivities(admin, context.packageName)
        launcher?.let { dpm.clearPackagePersistentPreferredActivities(admin, it.packageName) }
        val filter = IntentFilter(Intent.ACTION_MAIN).apply {
            addCategory(Intent.CATEGORY_HOME)
            addCategory(Intent.CATEGORY_DEFAULT)
        }
        val target = if (locked) ComponentName(context, MainActivity::class.java) else launcher
        target?.let { dpm.addPersistentPreferredActivity(admin, filter, it) }
    }

    /** Applique l'état courant. [launch] : ramène l'écran de devoir au premier plan s'il faut bloquer. */
    fun refresh(context: Context, launch: Boolean) {
        rememberOriginalLauncher(context)
        applyPolicies(context)
        val locked = !isUnlocked(context)
        setHome(context, locked)
        Scheduler.schedule(context)
        if (locked && launch) {
            context.startActivity(
                Intent(context, MainActivity::class.java).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
            )
        }
    }

    /** Supprime toutes les restrictions puis renonce au statut Device Owner (l'app devient désinstallable). */
    fun removeDeviceOwner(context: Context) {
        if (!isDeviceOwner(context)) return
        val dpm = dpm(context)
        val admin = admin(context)
        (RESTRICTIONS + UserManager.DISALLOW_DEBUGGING_FEATURES).forEach { dpm.clearUserRestriction(admin, it) }
        dpm.clearPackagePersistentPreferredActivities(admin, context.packageName)
        originalLauncher(context)?.let { dpm.clearPackagePersistentPreferredActivities(admin, it.packageName) }
        dpm.setLockTaskPackages(admin, emptyArray())
        @Suppress("DEPRECATION")
        dpm.clearDeviceOwnerApp(context.packageName)
    }
}
