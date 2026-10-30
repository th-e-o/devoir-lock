package app.devoirdujour

import android.app.Activity
import android.app.ActivityManager
import android.content.ActivityNotFoundException
import android.content.Intent
import android.graphics.Color
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.provider.MediaStore
import android.view.View
import android.webkit.WebResourceRequest
import android.webkit.WebView
import android.webkit.WebViewClient
import android.widget.Button
import android.widget.TextView
import android.widget.Toast
import androidx.core.content.FileProvider
import org.json.JSONObject
import java.io.File
import java.util.concurrent.Executors

class MainActivity : Activity() {
    private val handler = Handler(Looper.getMainLooper())
    private val io = Executors.newSingleThreadExecutor()
    private lateinit var prefs: Prefs

    private lateinit var status: TextView
    private lateinit var exercise: WebView
    private lateinit var pagesLabel: TextView
    private lateinit var lockedActions: View
    private lateinit var takePhoto: Button
    private lateinit var send: Button
    private lateinit var clear: Button
    private lateinit var continueButton: Button

    private var webReady = false
    private var renderedEnonce: String? = null
    private var pendingPhoto: String? = null
    private var sending = false
    private var lastError: String? = null
    private var wasUnlocked = false

    private val poller = object : Runnable {
        override fun run() {
            sync()
            handler.postDelayed(this, POLL_MS)
        }
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_main)
        prefs = Prefs(this)
        pendingPhoto = savedInstanceState?.getString(KEY_PENDING_PHOTO)

        status = findViewById(R.id.status)
        exercise = findViewById(R.id.exercise)
        pagesLabel = findViewById(R.id.pages)
        lockedActions = findViewById(R.id.locked_actions)
        takePhoto = findViewById(R.id.take_photo)
        send = findViewById(R.id.send)
        clear = findViewById(R.id.clear)
        continueButton = findViewById(R.id.continue_button)

        requireViewById<TextView>(R.id.title).setOnLongClickListener {
            startActivity(Intent(this, AdminActivity::class.java))
            true
        }
        takePhoto.setOnClickListener { capturePage() }
        send.setOnClickListener { submit() }
        clear.setOnClickListener { clearPages() }
        requireViewById<Button>(R.id.call).setOnClickListener {
            try {
                startActivity(Intent(Intent.ACTION_DIAL))
            } catch (e: ActivityNotFoundException) {
                toast("Pas d'application téléphone")
            }
        }
        continueButton.setOnClickListener { openLauncher() }

        exercise.settings.javaScriptEnabled = true
        exercise.settings.allowFileAccess = false
        exercise.webViewClient = object : WebViewClient() {
            // Aucune navigation possible depuis l'énoncé.
            override fun shouldOverrideUrlLoading(view: WebView, request: WebResourceRequest) = true

            override fun onPageFinished(view: WebView, url: String) {
                webReady = true
                renderedEnonce = null
                updateUi()
            }
        }
        exercise.loadUrl("file:///android_asset/web/devoir.html")
    }

    override fun onSaveInstanceState(outState: Bundle) {
        super.onSaveInstanceState(outState)
        outState.putString(KEY_PENDING_PHOTO, pendingPhoto)
    }

    override fun onResume() {
        super.onResume()
        LockManager.refresh(this, launch = false)
        wasUnlocked = LockManager.isUnlocked(this)
        updateUi()
        handler.post(poller)
    }

    override fun onPause() {
        super.onPause()
        handler.removeCallbacks(poller)
    }

    @Deprecated("Deprecated in Java")
    override fun onBackPressed() {
        if (LockManager.isUnlocked(this)) {
            @Suppress("DEPRECATION")
            super.onBackPressed()
        }
    }

    // --- synchronisation -------------------------------------------------------

    private fun sync() {
        io.execute {
            val error = try {
                LockManager.applyServerState(this, Api.today())
                null
            } catch (e: Exception) {
                e.message ?: e.javaClass.simpleName
            }
            runOnUiThread {
                lastError = error
                val unlocked = LockManager.isUnlocked(this)
                updateUi()
                if (unlocked && !wasUnlocked) {
                    toast("Devoir validé, bonne journée !")
                    openLauncher()
                }
                wasUnlocked = unlocked
            }
        }
    }

    private fun currentState(): JSONObject? =
        prefs.cachedState?.let { runCatching { JSONObject(it) }.getOrNull() }

    // --- affichage ---------------------------------------------------------------

    private fun updateUi() {
        val state = currentState()
        val unlocked = LockManager.isUnlocked(this)
        LockManager.setHome(this, locked = !unlocked)

        if (unlocked) {
            stopKiosk()
            setStatus("✅ Devoir du jour validé. Le téléphone est débloqué jusqu'à minuit.", "#D7F5DD")
            lockedActions.visibility = View.GONE
            continueButton.visibility = View.VISIBLE
        } else {
            startKiosk()
            lockedActions.visibility = View.VISIBLE
            continueButton.visibility = View.GONE
            setStatus(lockedMessage(state), "#FFE9C7")
        }

        val pages = prefs.pendingPages.filter { File(it).exists() }
        pagesLabel.text = if (pages.isEmpty()) "Aucune page photographiée" else "${pages.size} page(s) prête(s) à envoyer"
        send.isEnabled = pages.isNotEmpty() && !sending
        clear.isEnabled = pages.isNotEmpty() && !sending
        takePhoto.isEnabled = !sending
        send.text = if (sending) "Envoi…" else getString(R.string.send)

        val enonce = state?.optString("enonce").orEmpty()
        if (webReady && enonce.isNotEmpty() && enonce != renderedEnonce) {
            val data = JSONObject()
                .put("titre", state!!.optString("titre"))
                .put("enonce", enonce)
                .put("duree_min", state.optInt("duree_min"))
            exercise.evaluateJavascript("render($data)", null)
            renderedEnonce = enonce
        }
    }

    private fun lockedMessage(state: JSONObject?): String {
        val lines = mutableListOf<String>()
        if (!LockManager.isDeviceOwner(this)) {
            lines += "⚠️ Application non configurée comme propriétaire de l'appareil : le blocage est inactif."
        }
        val today = java.time.LocalDate.now().toString()
        val sameDay = state?.optString("date") == today
        val copy = state?.optJSONObject("derniere_copie")
        lines += when {
            state == null || !sameDay -> "Connexion au serveur…"
            state.optString("status") == "en_preparation" -> "Le devoir du jour est en préparation…"
            state.optString("status") == "en_correction" ->
                "⏳ Copie envoyée, en attente de correction. Tu peux renvoyer une version corrigée si besoin."
            state.optString("status") == "valide" ->
                "Devoir validé par le serveur, mais le jeton de déblocage est refusé " +
                    "(vérifier la clé publique, l'identifiant d'appareil et l'heure du téléphone)."
            copy?.optString("status") == "refuse" ->
                "❌ Copie refusée. " + copy.optString("feedback").ifEmpty { "Reprends le devoir et renvoie-le." }
            else -> "Fais le devoir sur ton cahier, photographie chaque page puis envoie."
        }
        lastError?.let { lines += "Hors ligne : $it" }
        return lines.joinToString("\n\n")
    }

    private fun setStatus(text: String, color: String) {
        status.text = text
        status.setBackgroundColor(Color.parseColor(color))
    }

    // --- mode kiosque -------------------------------------------------------------

    private fun lockTaskActive(): Boolean =
        getSystemService(ActivityManager::class.java)!!.lockTaskModeState != ActivityManager.LOCK_TASK_MODE_NONE

    private fun startKiosk() {
        if (LockManager.isDeviceOwner(this) && !lockTaskActive()) {
            try {
                startLockTask()
            } catch (e: IllegalStateException) {
                // Activité pas encore au premier plan : nouvel essai au prochain rafraîchissement.
            }
        }
    }

    private fun stopKiosk() {
        if (lockTaskActive()) {
            try {
                stopLockTask()
            } catch (e: Exception) {
                // Le kiosque appartient à une autre tâche : rien à faire ici.
            }
        }
    }

    private fun openLauncher() {
        val launcher = LockManager.originalLauncher(this) ?: return
        try {
            startActivity(
                Intent(Intent.ACTION_MAIN).addCategory(Intent.CATEGORY_HOME).setComponent(launcher)
                    .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
            )
        } catch (e: ActivityNotFoundException) {
            toast("Lanceur introuvable")
        }
    }

    // --- photos -------------------------------------------------------------------

    private fun capturePage() {
        val dir = File(filesDir, "pages").apply { mkdirs() }
        val file = File(dir, "page_${System.currentTimeMillis()}.jpg")
        val uri = FileProvider.getUriForFile(this, "$packageName.files", file)
        val intent = Intent(MediaStore.ACTION_IMAGE_CAPTURE)
            .putExtra(MediaStore.EXTRA_OUTPUT, uri)
            .addFlags(Intent.FLAG_GRANT_WRITE_URI_PERMISSION or Intent.FLAG_GRANT_READ_URI_PERMISSION)
        pendingPhoto = file.absolutePath
        try {
            @Suppress("DEPRECATION")
            startActivityForResult(intent, REQUEST_PHOTO)
        } catch (e: ActivityNotFoundException) {
            pendingPhoto = null
            toast("Pas d'appareil photo disponible")
        }
    }

    @Deprecated("Deprecated in Java")
    override fun onActivityResult(requestCode: Int, resultCode: Int, data: Intent?) {
        @Suppress("DEPRECATION")
        super.onActivityResult(requestCode, resultCode, data)
        if (requestCode != REQUEST_PHOTO) return
        val path = pendingPhoto ?: return
        pendingPhoto = null
        val file = File(path)
        if (resultCode == RESULT_OK && file.length() > 0) {
            prefs.pendingPages = prefs.pendingPages + path
        } else {
            file.delete()
        }
        updateUi()
    }

    private fun clearPages() {
        prefs.pendingPages.forEach { File(it).delete() }
        prefs.pendingPages = emptyList()
        updateUi()
    }

    private fun submit() {
        val pages = prefs.pendingPages.map(::File).filter { it.exists() }
        if (pages.isEmpty()) return
        sending = true
        updateUi()
        io.execute {
            val error = try {
                Api.submit(pages)
                LockManager.applyServerState(this, Api.today())
                null
            } catch (e: Exception) {
                e.message ?: e.javaClass.simpleName
            }
            runOnUiThread {
                sending = false
                if (error == null) {
                    clearPages()
                    toast("Copie envoyée")
                } else {
                    toast("Envoi impossible : $error")
                    updateUi()
                }
            }
        }
    }

    private fun toast(text: String) = Toast.makeText(this, text, Toast.LENGTH_LONG).show()

    companion object {
        private const val POLL_MS = 30_000L
        private const val REQUEST_PHOTO = 1
        private const val KEY_PENDING_PHOTO = "pending_photo"
    }
}
