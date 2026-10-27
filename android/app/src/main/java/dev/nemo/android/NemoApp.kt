package dev.nemo.android

import android.app.Application
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import androidx.core.app.NotificationCompat
import dev.nemo.android.agent.Agents
import dev.nemo.android.agent.Approvals
import dev.nemo.android.agent.Sentinel
import dev.nemo.android.data.ConversationStore
import dev.nemo.android.data.MemoryStore
import dev.nemo.android.data.Settings
import dev.nemo.android.hub.HubClient
import dev.nemo.android.llm.AnthropicChat
import dev.nemo.android.llm.ChatModel
import dev.nemo.android.llm.LlmError
import dev.nemo.android.llm.OpenAIChat
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.launch

/** Everything the app holds, wired once. */
class AppGraph(val context: Context) {
    val settings = Settings(context)
    val store = ConversationStore(context.filesDir)
    val memory = MemoryStore(context.filesDir)
    val approvals = Approvals()
    val sentinel = Sentinel(this)
    val agents = Agents(this)
    val hub = HubClient(this)
    /** true while the app is on screen; questions that arrive otherwise become notifications */
    val foreground = MutableStateFlow(false)

    fun model(): ChatModel {
        val c = settings.config.value
        if (!c.ready) throw LlmError("No model yet: open Settings and pick a provider, or sign in to a relay.")
        if (c.provider == "relay") return OpenAIChat("${c.relayUrl.trimEnd('/')}/v1", settings.hubToken.orEmpty(), "default")
        val key = settings.apiKey.orEmpty()
        return if (c.kind == "anthropic") AnthropicChat(c.baseUrl, key, c.model) else OpenAIChat(c.baseUrl, key, c.model)
    }
}

class NemoApp : Application() {
    lateinit var graph: AppGraph
        private set

    override fun onCreate() {
        super.onCreate()
        graph = AppGraph(this)
        val nm = getSystemService(NotificationManager::class.java)
        nm.createNotificationChannel(NotificationChannel(CHANNEL_UPDATES, "Nemo updates", NotificationManager.IMPORTANCE_DEFAULT))
        nm.createNotificationChannel(NotificationChannel(CHANNEL_APPROVALS, "Approvals", NotificationManager.IMPORTANCE_HIGH))
        nm.createNotificationChannel(NotificationChannel(CHANNEL_HUB, "Connection to your devices", NotificationManager.IMPORTANCE_MIN))
        graph.agents.scope.launch {
            var seen = emptySet<String>()
            graph.approvals.pending.collect { list ->
                val fresh = list.filter { it.id !in seen }
                seen = list.map { it.id }.toSet()
                if (!graph.foreground.value) fresh.forEach { notifyApproval(nm, it.id.hashCode(), it.reason, it.from) }
            }
        }
    }

    private fun notifyApproval(nm: NotificationManager, id: Int, reason: String, from: String) {
        val open = PendingIntent.getActivity(this, 0, Intent(this, MainActivity::class.java).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK), PendingIntent.FLAG_IMMUTABLE)
        nm.notify(id, NotificationCompat.Builder(this, CHANNEL_APPROVALS)
            .setSmallIcon(R.mipmap.ic_launcher)
            .setContentTitle(if (from == "this phone") "Nemo needs your OK" else "$from needs your OK")
            .setContentText(reason)
            .setStyle(NotificationCompat.BigTextStyle().bigText(reason))
            .setContentIntent(open)
            .setAutoCancel(true)
            .setPriority(NotificationCompat.PRIORITY_HIGH)
            .build())
    }

    companion object {
        const val CHANNEL_UPDATES = "updates"
        const val CHANNEL_APPROVALS = "approvals"
        const val CHANNEL_HUB = "hub"
    }
}
