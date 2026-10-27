package dev.nemo.android.data

import android.content.Context
import android.os.Build
import android.security.keystore.KeyGenParameterSpec
import android.security.keystore.KeyProperties
import android.util.Base64
import dev.nemo.android.agent.Mode
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import java.security.KeyStore
import java.util.UUID
import javax.crypto.Cipher
import javax.crypto.KeyGenerator
import javax.crypto.SecretKey
import javax.crypto.spec.GCMParameterSpec

/** Secrets encrypted with an AES-GCM key that never leaves the Android Keystore. */
object Secrets {
    private const val ALIAS = "nemo_secrets"

    private fun key(): SecretKey {
        val ks = KeyStore.getInstance("AndroidKeyStore").apply { load(null) }
        (ks.getEntry(ALIAS, null) as? KeyStore.SecretKeyEntry)?.let { return it.secretKey }
        val gen = KeyGenerator.getInstance(KeyProperties.KEY_ALGORITHM_AES, "AndroidKeyStore")
        gen.init(KeyGenParameterSpec.Builder(ALIAS, KeyProperties.PURPOSE_ENCRYPT or KeyProperties.PURPOSE_DECRYPT)
            .setBlockModes(KeyProperties.BLOCK_MODE_GCM)
            .setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE)
            .build())
        return gen.generateKey()
    }

    fun encrypt(plain: String): String {
        val cipher = Cipher.getInstance("AES/GCM/NoPadding").apply { init(Cipher.ENCRYPT_MODE, key()) }
        val data = cipher.iv + cipher.doFinal(plain.toByteArray())
        return Base64.encodeToString(data, Base64.NO_WRAP)
    }

    fun decrypt(sealed: String): String? = runCatching {
        val data = Base64.decode(sealed, Base64.NO_WRAP)
        val cipher = Cipher.getInstance("AES/GCM/NoPadding")
        cipher.init(Cipher.DECRYPT_MODE, key(), GCMParameterSpec(128, data, 0, 12))
        String(cipher.doFinal(data, 12, data.size - 12))
    }.getOrNull()
}

data class Provider(val id: String, val kind: String, val baseUrl: String, val model: String)

val PROVIDERS = listOf(
    Provider("relay", "openai", "", "default"),
    Provider("anthropic", "anthropic", "https://api.anthropic.com", "claude-sonnet-5-5"),
    Provider("openai", "openai", "https://api.openai.com/v1", "gpt-4.1-mini"),
    Provider("deepseek", "openai", "https://api.deepseek.com/v1", "deepseek-chat"),
    Provider("bailian", "openai", "https://dashscope.aliyuncs.com/compatible-mode/v1", "qwen-plus"),
    Provider("openrouter", "openai", "https://openrouter.ai/api/v1", "openrouter/auto"),
    Provider("gemini", "openai", "https://generativelanguage.googleapis.com/v1beta/openai", "gemini-2.5-flash"),
    Provider("custom", "openai", "", ""),
)

data class Config(
    val name: String = "Nemo",
    val provider: String = "anthropic",
    val kind: String = "anthropic",
    val baseUrl: String = "https://api.anthropic.com",
    val model: String = "claude-sonnet-5-5",
    val hasKey: Boolean = false,
    val mode: Mode = Mode.ASK,
    val relayUrl: String = "",
    val signedIn: Boolean = false,
    val deviceId: String = "",
    val deviceName: String = "${Build.MANUFACTURER} ${Build.MODEL}",
) {
    val ready get() = if (provider == "relay") signedIn else model.isNotBlank() && baseUrl.isNotBlank() && hasKey
}

class Settings(context: Context) {
    private val prefs = context.getSharedPreferences("nemo", Context.MODE_PRIVATE)
    private val _config = MutableStateFlow(read())
    val config: StateFlow<Config> = _config

    private fun read(): Config {
        var id = prefs.getString("device_id", null)
        if (id == null) {
            id = UUID.randomUUID().toString().replace("-", "")
            prefs.edit().putString("device_id", id).apply()
        }
        val d = Config()
        return Config(
            name = prefs.getString("name", d.name)!!,
            provider = prefs.getString("provider", d.provider)!!,
            kind = prefs.getString("kind", d.kind)!!,
            baseUrl = prefs.getString("base_url", d.baseUrl)!!,
            model = prefs.getString("model", d.model)!!,
            hasKey = prefs.contains("api_key"),
            mode = runCatching { Mode.valueOf(prefs.getString("mode", "ASK")!!) }.getOrDefault(Mode.ASK),
            relayUrl = prefs.getString("relay_url", "")!!,
            signedIn = prefs.contains("hub_token"),
            deviceId = id,
            deviceName = prefs.getString("device_name", d.deviceName)!!,
        )
    }

    fun update(block: Config.() -> Config) {
        val c = _config.value.block()
        prefs.edit()
            .putString("name", c.name).putString("provider", c.provider).putString("kind", c.kind)
            .putString("base_url", c.baseUrl).putString("model", c.model).putString("mode", c.mode.name)
            .putString("relay_url", c.relayUrl).putString("device_name", c.deviceName)
            .apply()
        _config.value = read()
    }

    var apiKey: String?
        get() = prefs.getString("api_key", null)?.let(Secrets::decrypt)
        set(v) { prefs.edit().apply { if (v.isNullOrBlank()) remove("api_key") else putString("api_key", Secrets.encrypt(v)) }.apply(); _config.value = read() }

    var hubToken: String?
        get() = prefs.getString("hub_token", null)?.let(Secrets::decrypt)
        set(v) { prefs.edit().apply { if (v.isNullOrBlank()) remove("hub_token") else putString("hub_token", Secrets.encrypt(v)) }.apply(); _config.value = read() }

    /** Approvals answered with "always", by grant key. */
    var alwaysGrants: Set<String>
        get() = prefs.getStringSet("grants_always", emptySet())!!
        set(v) = prefs.edit().putStringSet("grants_always", v).apply()
}
