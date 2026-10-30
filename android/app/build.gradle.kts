import java.security.MessageDigest
import java.util.Properties

plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
}

val devoir = Properties().apply {
    val f = rootProject.file("devoir.properties")
    if (f.exists()) f.inputStream().use { load(it) }
    else logger.warn("devoir.properties absent : copiez devoir.properties.example")
}

fun prop(name: String, default: String = ""): String = devoir.getProperty(name, default).trim()

fun quoted(value: String) = "\"" + value.replace("\\", "\\\\").replace("\"", "\\\"") + "\""

fun sha256(value: String): String =
    MessageDigest.getInstance("SHA-256").digest(value.toByteArray()).joinToString("") { "%02x".format(it) }

android {
    namespace = "app.devoirdujour"
    compileSdk = 35

    defaultConfig {
        applicationId = "app.devoirdujour"
        minSdk = 28
        targetSdk = 35
        versionCode = 1
        versionName = "0.1"

        buildConfigField("String", "SERVER_URL", quoted(prop("SERVER_URL").trimEnd('/')))
        buildConfigField("String", "DEVICE_TOKEN", quoted(prop("DEVICE_TOKEN")))
        buildConfigField("String", "DEVICE_ID", quoted(prop("DEVICE_ID", "telephone-eleve")))
        buildConfigField("String", "SERVER_PUBLIC_KEY", quoted(prop("SERVER_PUBLIC_KEY")))
        buildConfigField("String", "ADMIN_PIN_SHA256", quoted(sha256(prop("ADMIN_PIN", "0000"))))
        buildConfigField("String", "EXTRA_ALLOWED_PACKAGES", quoted(prop("EXTRA_ALLOWED_PACKAGES")))
    }

    buildFeatures {
        buildConfig = true
    }

    buildTypes {
        release {
            isMinifyEnabled = false
            // Usage domestique : on signe la release avec la clé de debug pour simplifier l'installation.
            signingConfig = signingConfigs.getByName("debug")
        }
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    kotlinOptions {
        jvmTarget = "17"
    }
}

dependencies {
    implementation("androidx.core:core-ktx:1.13.1")
    implementation("com.squareup.okhttp3:okhttp:4.12.0")
}
