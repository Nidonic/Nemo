package dev.nemo.android.hub

import android.os.BatteryManager
import android.os.Build
import dev.nemo.android.AppGraph
import dev.nemo.android.agent.NotifyTool
import dev.nemo.android.agent.OpenAppTool
import dev.nemo.android.agent.RemoteEmit
import dev.nemo.android.agent.ScreenTool
import dev.nemo.android.agent.Tool
import dev.nemo.android.agent.ToolCtx
import dev.nemo.android.data.ToolCall
import dev.nemo.android.data.json
import dev.nemo.android.data.newId
import dev.nemo.android.llm.http
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.launch
import kotlinx.coroutines.withTimeoutOrNull
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.booleanOrNull
import kotlinx.serialization.json.buildJsonArray
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.contentOrNull
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.put
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import okhttp3.Response
import okhttp3.WebSocket
import okhttp3.WebSocketListener
import java.util.concurrent.ConcurrentHashMap

data class Device(val id: String, val name: String, val kind: String, val os: String, val online: Boolean, val actions: List<String>)

private fun JsonElement?.str(): String? = (this as? JsonPrimitive)?.contentOrNull

fun frame(type: String, action: String = "", to: String = "", id: String = newId("f"), args: JsonObject? = null, body: JsonObject? = null) =
    buildJsonObject {
        put("type", type); put("id", id); put("from", ""); put("to", to); put("action", action)
        put("args", args ?: JsonObject(emptyMap())); put("body", body ?: JsonObject(emptyMap()))
    }

fun hubUrl(base: String) = base.trimEnd('/').replaceFirst(Regex("^http"), "ws") + "/v1/hub"

/** This phone's seat on the relay hub. */
class HubClient(private val graph: AppGraph) {
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)
    private val _online = MutableStateFlow(false)
    val online: StateFlow<Boolean> = _online
    private val _devices = MutableStateFlow<List<Device>>(emptyList())
    val devices: StateFlow<List<Device>> = _devices
    private val _error = MutableStateFlow("")
    val error: StateFlow<String> = _error

    private var ws: WebSocket? = null
    private var loop: Job? = null
    private val pending = ConcurrentHashMap<String, CompletableDeferred<JsonObject>>()
    private val streams = ConcurrentHashMap<String, suspend (JsonObject) -> Unit>()
    private val running = ConcurrentHashMap<String, Job>()
    val actions = listOf("info", "notify", "screen", "open", "task")

    fun start() {
        if (loop?.isActive == true) return
        loop = scope.launch {
            var backoff = 1000L
            while (true) {
                val cfg = graph.settings.config.value
                val token = graph.settings.hubToken
                if (cfg.relayUrl.isBlank() || token.isNullOrBlank()) { delay(3000); continue }
                val closed = CompletableDeferred<Unit>()
                ws = http.newWebSocket(Request.Builder().url(hubUrl(cfg.relayUrl)).header("Authorization", "Bearer $token").build(),
                    object : WebSocketListener() {
                        override fun onOpen(webSocket: WebSocket, response: Response) {
                            _online.value = true
                            _error.value = ""
                            backoff = 1000L
                            webSocket.send(frame("event", "hello", body = buildJsonObject {
                                put("device", buildJsonObject {
                                    put("id", cfg.deviceId); put("name", cfg.deviceName); put("kind", "phone")
                                    put("os", "Android ${Build.VERSION.RELEASE}"); put("version", "0.2.0")
                                    put("actions", buildJsonArray { actions.forEach { add(JsonPrimitive(it)) } })
                                })
                            }).toString())
                        }

                        override fun onMessage(webSocket: WebSocket, text: String) {
                            val msg = runCatching { json.parseToJsonElement(text).jsonObject }.getOrNull() ?: return
                            scope.launch { dispatch(msg) }
                        }

                        override fun onClosed(webSocket: WebSocket, code: Int, reason: String) { closed.complete(Unit) }

                        override fun onFailure(webSocket: WebSocket, t: Throwable, response: Response?) {
                            _error.value = if (response?.code == 401 || response?.code == 403) "Signed out by the relay; sign in again." else (t.message ?: "connection failed")
                            closed.complete(Unit)
                        }
                    })
                closed.await()
                _online.value = false
                pending.values.forEach { it.complete(buildJsonObject { put("ok", false); put("error", "The hub connection dropped.") }) }
                pending.clear()
                delay(backoff)
                backoff = (backoff * 2).coerceAtMost(30_000)
            }
        }
    }

    fun stop() {
        loop?.cancel()
        loop = null
        ws?.close(1000, "bye")
        ws = null
        _online.value = false
    }

    private fun send(msg: JsonObject) {
        ws?.send(msg.toString()) ?: throw IllegalStateException("Not connected to the hub.")
    }

    private suspend fun dispatch(msg: JsonObject) {
        val id = msg["id"].str().orEmpty()
        val body = msg["body"] as? JsonObject ?: JsonObject(emptyMap())
        val args = msg["args"] as? JsonObject ?: JsonObject(emptyMap())
        when (msg["type"].str()) {
            "result" -> { streams.remove(id); pending.remove(id)?.complete(body) }
            "event" -> {
                if (msg["action"].str() == "devices") _devices.value = (body["devices"] as? JsonArray).orEmpty().map { parseDevice(it.jsonObject) }
                streams[id]?.invoke(msg)
            }
            "approve" -> graph.approvals.resolve(args["approval_id"].str().orEmpty(), args["allow"].let { (it as? JsonPrimitive)?.booleanOrNull } ?: false,
                args["scope"].str() ?: "once")
            "stop" -> args["call"].str()?.let { running[it]?.cancel() }
            "call" -> {
                val from = msg["from"].str().orEmpty()
                val action = msg["action"].str().orEmpty()
                val job = scope.launch {
                    val answer = try {
                        answer(action, args, from) { stage, extra ->
                            runCatching { send(frame("event", action, to = from, id = id, body = JsonObject(mapOf("stage" to JsonPrimitive(stage)) + extra))) }
                        }
                    } catch (e: CancellationException) {
                        buildJsonObject { put("ok", false); put("error", "stopped") }
                    } catch (e: Exception) {
                        buildJsonObject { put("ok", false); put("error", "${e.javaClass.simpleName}: ${e.message}") }
                    }
                    runCatching { send(frame("result", action, to = from, id = id, body = answer)) }
                }
                running[id] = job
                job.invokeOnCompletion { running.remove(id) }
            }
        }
    }

    private fun parseDevice(o: JsonObject) = Device(
        o["id"].str().orEmpty(), o["name"].str().orEmpty(), o["kind"].str().orEmpty(), o["os"].str().orEmpty(),
        o["online"].let { (it as? JsonPrimitive)?.booleanOrNull } ?: false,
        (o["actions"] as? JsonArray).orEmpty().mapNotNull { it.str() },
    )

    /** Calls from the account's other devices. Everything that acts goes through this phone's Sentinel. */
    private suspend fun answer(action: String, args: JsonObject, from: String, emit: RemoteEmit): JsonObject {
        val cid = "hub-${from.take(24)}"
        suspend fun guarded(tool: Tool, a: JsonObject) = graph.sentinel.guard(ToolCall(name = tool.name, arguments = a), tool, ToolCtx(graph, cid, "hub:$from"), emit)
        return when (action) {
            "info" -> {
                val bm = graph.context.getSystemService(BatteryManager::class.java)
                buildJsonObject {
                    put("ok", true); put("name", graph.settings.config.value.deviceName); put("kind", "phone")
                    put("os", "Android ${Build.VERSION.RELEASE}"); put("model", "${Build.MANUFACTURER} ${Build.MODEL}")
                    put("battery", bm.getIntProperty(BatteryManager.BATTERY_PROPERTY_CAPACITY)); put("charging", bm.isCharging)
                }
            }
            "notify" -> {
                guarded(NotifyTool(), buildJsonObject { put("title", args["title"].str() ?: "Nemo"); put("text", args["body"].str().orEmpty()) })
                buildJsonObject { put("ok", true) }
            }
            "screen" -> {
                val out = guarded(ScreenTool(), JsonObject(emptyMap()))
                val shot = out.shot ?: return buildJsonObject { put("ok", false); put("error", out.text) }
                buildJsonObject { put("ok", true); put("image", shot.jpegBase64); put("mime", "image/jpeg"); put("width", shot.width); put("height", shot.height) }
            }
            "open" -> {
                val target = args["target"].str().orEmpty()
                val out = guarded(OpenAppTool(), buildJsonObject { if (":" in target) put("url", target) else put("name", target) })
                buildJsonObject { put("ok", out.ok); put("output", out.text) }
            }
            "task" -> {
                val convId = "t_hub_" + (from + args["conversation"].str().orEmpty()).takeLast(20).filter { it.isLetterOrDigit() }
                if (graph.store.get(convId) == null) graph.store.create("From another device: ${args["text"].str().orEmpty().take(40)}", "hub:$from", convId)
                val (status, text) = graph.agents.runTurn(convId, args["text"].str().orEmpty(), emit)
                if (status == "ok") buildJsonObject { put("ok", true); put("text", text); put("conversation", convId) }
                else buildJsonObject { put("ok", false); put("error", status) }
            }
            else -> buildJsonObject { put("ok", false); put("error", "This phone does not support $action.") }
        }
    }

    // ------------------------------------------------------------------ calling other devices

    suspend fun call(to: String, action: String, args: JsonObject, timeoutSec: Long = 60, onEvent: (suspend (JsonObject) -> Unit)? = null): JsonObject {
        val msg = frame("call", action, to = to, args = args)
        val id = msg["id"].str()!!
        val answer = CompletableDeferred<JsonObject>()
        pending[id] = answer
        onEvent?.let { streams[id] = it }
        return try {
            send(msg)
            withTimeoutOrNull(timeoutSec * 1000) { answer.await() } ?: buildJsonObject { put("ok", false); put("error", "$action timed out.") }
        } catch (e: IllegalStateException) {
            buildJsonObject { put("ok", false); put("error", e.message ?: "offline") }
        } finally {
            pending.remove(id)
            streams.remove(id)
        }
    }

    fun approve(to: String, approvalId: String, allow: Boolean, scope: String) {
        runCatching { send(frame("approve", to = to, args = buildJsonObject { put("approval_id", approvalId); put("allow", allow); put("scope", scope) })) }
    }

    fun find(nameOrId: String): Device? {
        val key = nameOrId.removePrefix("@").lowercase()
        return _devices.value.firstOrNull { it.id == nameOrId || it.name.lowercase() == key }
            ?: _devices.value.firstOrNull { it.name.lowercase().startsWith(key) || it.os.lowercase().startsWith(key) }
    }
}

/** Relay sign-in with a one-time code. */
object Account {
    private val JSON = "application/json".toMediaType()

    private fun post(url: String, body: JsonObject): JsonObject {
        http.newCall(Request.Builder().url(url).post(body.toString().toRequestBody(JSON)).build()).execute().use { res ->
            val data = runCatching { json.parseToJsonElement(res.body?.string().orEmpty()).jsonObject }.getOrDefault(JsonObject(emptyMap()))
            if (!res.isSuccessful) throw IllegalStateException(data["error"].str() ?: "HTTP ${res.code}")
            return data
        }
    }

    fun sendCode(relay: String, identifier: String): String =
        post("${relay.trimEnd('/')}/v1/auth/code", buildJsonObject { put("identifier", identifier) })["hint"].str() ?: identifier

    fun verify(relay: String, identifier: String, code: String, deviceId: String, deviceName: String): String =
        post("${relay.trimEnd('/')}/v1/auth/verify", buildJsonObject {
            put("identifier", identifier); put("code", code)
            put("device", buildJsonObject { put("id", deviceId); put("name", deviceName); put("kind", "phone") })
        })["token"].str() ?: throw IllegalStateException("The relay sent no token.")
}
