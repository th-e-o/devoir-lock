package app.devoirdujour

import okhttp3.MediaType.Companion.toMediaType
import okhttp3.MultipartBody
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.asRequestBody
import okhttp3.Response
import org.json.JSONObject
import java.io.File
import java.io.IOException
import java.util.concurrent.TimeUnit

class ApiException(message: String) : IOException(message)

/** Appels HTTP bloquants : à exécuter hors du thread principal. */
object Api {
    private val client = OkHttpClient.Builder()
        .connectTimeout(15, TimeUnit.SECONDS)
        .readTimeout(60, TimeUnit.SECONDS)
        .writeTimeout(180, TimeUnit.SECONDS)
        .build()

    private fun request(path: String) = Request.Builder()
        .url(BuildConfig.SERVER_URL + path)
        .header("Authorization", "Bearer " + BuildConfig.DEVICE_TOKEN)

    fun today(): JSONObject = client.newCall(request("/api/today").get().build()).execute().use(::parse)

    fun submit(pages: List<File>): JSONObject {
        val body = MultipartBody.Builder().setType(MultipartBody.FORM).apply {
            pages.forEach { addFormDataPart("photos", it.name, it.asRequestBody("image/jpeg".toMediaType())) }
        }.build()
        return client.newCall(request("/api/today/submissions").post(body).build()).execute().use(::parse)
    }

    private fun parse(response: Response): JSONObject {
        val text = response.body?.string().orEmpty()
        val json = try { JSONObject(text) } catch (e: Exception) { JSONObject() }
        if (!response.isSuccessful) {
            throw ApiException(json.optString("detail").ifEmpty { "Erreur serveur (${response.code})" })
        }
        return json
    }
}
