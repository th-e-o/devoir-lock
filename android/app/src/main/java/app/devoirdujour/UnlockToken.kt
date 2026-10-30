package app.devoirdujour

import android.util.Base64
import org.json.JSONObject
import java.security.KeyFactory
import java.security.Signature
import java.security.spec.X509EncodedKeySpec
import java.time.LocalDate

/**
 * Jeton signé par le serveur (ECDSA P-256) : base64url(payload).base64url(signature DER).
 * Le payload contient l'identifiant de l'appareil et la date : un jeton ne débloque que ce jour-là.
 */
object UnlockToken {
    private const val B64URL = Base64.URL_SAFE or Base64.NO_PADDING or Base64.NO_WRAP

    fun isValidForToday(token: String?, today: LocalDate = LocalDate.now()): Boolean {
        if (token.isNullOrBlank()) return false
        return try {
            val parts = token.split(".")
            if (parts.size != 2) return false
            val body = Base64.decode(parts[0], B64URL)
            val signature = Base64.decode(parts[1], B64URL)
            val keyBytes = Base64.decode(BuildConfig.SERVER_PUBLIC_KEY, Base64.DEFAULT)
            val publicKey = KeyFactory.getInstance("EC").generatePublic(X509EncodedKeySpec(keyBytes))
            val verifier = Signature.getInstance("SHA256withECDSA")
            verifier.initVerify(publicKey)
            verifier.update(body)
            if (!verifier.verify(signature)) return false
            val payload = JSONObject(String(body, Charsets.UTF_8))
            payload.getString("device") == BuildConfig.DEVICE_ID && payload.getString("date") == today.toString()
        } catch (e: Exception) {
            false
        }
    }
}
