package dev.nemo.android.agent

import android.os.Build
import dev.nemo.android.AppGraph
import dev.nemo.android.data.Msg
import dev.nemo.android.data.ToolCall
import dev.nemo.android.data.newId
import dev.nemo.android.hands.HandsService
import dev.nemo.android.hands.HandsUnavailable
import dev.nemo.android.hub.Device
import dev.nemo.android.llm.ChatModel
import dev.nemo.android.llm.LlmError
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.channels.Channel
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.buildJsonArray
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.contentOrNull
import kotlinx.serialization.json.jsonArray
import kotlinx.serialization.json.jsonPrimitive
import kotlinx.serialization.json.put
import java.time.ZonedDateTime
import java.util.concurrent.ConcurrentHashMap

/** Sends progress of a task that another device asked for back to that device. */
typealias RemoteEmit = suspend (stage: String, body: JsonObject) -> Unit

class StuckError : Exception("I was repeating the same step, so I stopped. Tell me how to go on.")

// ---------------------------------------------------------------------- approvals

data class ApprovalRequest(
    val id: String,
    val conversationId: String,
    val tool: String,
    val preview: String,
    val risk: String,
    val reason: String,
    val warnings: List<String>,
    val grantKey: String,
    val scopes: List<String>,
    /** who is asking: "this phone", or the name of the device running the task */
    val from: String,
)

class Approvals {
    private val _pending = MutableStateFlow<List<ApprovalRequest>>(emptyList())
    val pending: StateFlow<List<ApprovalRequest>> = _pending
    private val waiters = ConcurrentHashMap<String, CompletableDeferred<Pair<Boolean, String>>>()
    private val mirrors = ConcurrentHashMap<String, suspend (Boolean, String) -> Unit>()

    suspend fun ask(req: ApprovalRequest): Pair<Boolean, String> {
        val answer = CompletableDeferred<Pair<Boolean, String>>()
        waiters[req.id] = answer
        _pending.update { it + req }
        try {
            return answer.await()
        } finally {
            waiters.remove(req.id)
            _pending.update { list -> list.filter { it.id != req.id } }
        }
    }

    /** Show a question that belongs to a task on another device; the answer goes back there. */
    fun mirror(req: ApprovalRequest, answer: suspend (Boolean, String) -> Unit) {
        mirrors[req.id] = answer
        _pending.update { it + req }
    }

    suspend fun resolve(id: String, allow: Boolean, scope: String = "once"): Boolean {
        waiters[id]?.let { it.complete(allow to scope); return true }
        val mirror = mirrors.remove(id) ?: return false
        _pending.update { list -> list.filter { it.id != id } }
        mirror(allow, scope)
        return true
    }

    fun dropMirrors(conversationId: String) {
        val gone = _pending.value.filter { it.conversationId == conversationId && mirrors.containsKey(it.id) }.map { it.id }.toSet()
        gone.forEach { mirrors.remove(it) }
        _pending.update { list -> list.filter { it.id !in gone } }
    }
}

// ---------------------------------------------------------------------- the Sentinel

class Sentinel(private val graph: AppGraph) {
    private val tainted = ConcurrentHashMap.newKeySet<String>()
    private val session = ConcurrentHashMap.newKeySet<String>()
    private val perConversation = ConcurrentHashMap<String, MutableSet<String>>()

    fun isTainted(cid: String) = cid in tainted

    private fun covered(key: String, cid: String): Boolean {
        val tool = key.substringBefore(":")
        val match = { g: String -> g == key || g == tool || g == "$tool:*" }
        return perConversation[cid]?.any(match) == true || session.any(match) || graph.settings.alwaysGrants.any(match)
    }

    suspend fun guard(call: ToolCall, tool: Tool, ctx: ToolCtx, remote: RemoteEmit?): ToolOut {
        val a = runCatching { tool.assess(call.arguments, ctx) }.getOrElse { return ToolOut("Error: bad arguments (${it.message}).", ok = false) }
        val v = Policy.evaluate(graph.settings.config.value.mode, a, isTainted(ctx.conversationId))
        if (v.decision == Decision.DENY) return refused(v.reason)
        if (v.decision == Decision.ASK) {
            val key = if (a.target.isBlank()) tool.name else "${tool.name}:${a.target}"
            if (v.mustAsk || !covered(key, ctx.conversationId)) {
                val req = ApprovalRequest(newId("ap_"), ctx.conversationId, tool.name, a.summary, a.risk.name.lowercase(), v.reason,
                    a.warnings.toList(), key, if (v.mustAsk) listOf("once") else listOf("once", "conversation", "session", "always"), "this phone")
                if (remote != null) remote("approval", buildJsonObject {
                    put("approval_id", req.id); put("tool", req.tool); put("preview", req.preview); put("risk", req.risk)
                    put("reason", req.reason); put("grant_key", req.grantKey)
                    put("warnings", buildJsonArray { req.warnings.forEach { add(JsonPrimitive(it)) } })
                    put("scopes", buildJsonArray { req.scopes.forEach { add(JsonPrimitive(it)) } })
                })
                val (allow, scope) = graph.approvals.ask(req)
                if (!allow) return refused("the user declined this action.")
                if (!v.mustAsk) when (scope) {
                    "conversation" -> perConversation.getOrPut(ctx.conversationId) { ConcurrentHashMap.newKeySet() } += key
                    "session" -> session += key
                    "always" -> graph.settings.alwaysGrants = graph.settings.alwaysGrants + key
                }
            }
        }
        val out = try {
            tool.run(call.arguments, ctx)
        } catch (e: CancellationException) {
            throw e
        } catch (e: HandsUnavailable) {
            ToolOut("Error: ${e.message}", ok = false)
        } catch (e: Exception) {
            ToolOut("Error: ${e.javaClass.simpleName}: ${e.message}", ok = false)
        }
        if (a.readsPrivate && out.ok) tainted += ctx.conversationId
        return out
    }

    private fun refused(why: String) = ToolOut("Refused by the Sentinel: $why Do not retry this call; tell the user or try another way.", ok = false)
}

// ---------------------------------------------------------------------- the agent

data class LiveTool(val id: String, val name: String, val summary: String, val status: String, val output: String = "")

data class LiveTurn(val text: String = "", val reasoning: String = "", val tools: List<LiveTool> = emptyList(), val remote: List<String> = emptyList())

class Agents(private val graph: AppGraph) {
    val scope = CoroutineScope(SupervisorJob() + Dispatchers.Default)
    private val tools = phoneTools().associateBy { it.name }
    private val jobs = ConcurrentHashMap<String, Job>()
    private val inboxes = ConcurrentHashMap<String, Channel<String>>()
    private val takeoverWaiters = ConcurrentHashMap<String, CompletableDeferred<String>>()

    private val _live = MutableStateFlow<Map<String, LiveTurn>>(emptyMap())
    val live: StateFlow<Map<String, LiveTurn>> = _live
    private val _running = MutableStateFlow<Set<String>>(emptySet())
    val running: StateFlow<Set<String>> = _running
    private val _hands = MutableStateFlow<Map<String, String>>(emptyMap())
    val hands: StateFlow<Map<String, String>> = _hands
    private val _takeovers = MutableStateFlow<Map<String, String>>(emptyMap())
    val takeovers: StateFlow<Map<String, String>> = _takeovers

    private fun live(cid: String, f: (LiveTurn) -> LiveTurn) = _live.update { it + (cid to f(it[cid] ?: LiveTurn())) }

    /** Start a turn, or add the text to the one already running. */
    fun send(cid: String, text: String): String {
        if (cid in _running.value) {
            inboxes[cid]?.trySend(text)
            return "queued"
        }
        val mention = Regex("^@(\\S+)\\s+(.+)", RegexOption.DOT_MATCHES_ALL).find(text.trim())
        val device = mention?.let { graph.hub.find(it.groupValues[1]) }?.takeIf { it.id != graph.settings.config.value.deviceId }
        _running.update { it + cid }
        inboxes[cid] = Channel(Channel.UNLIMITED) // ready before the turn starts, so a quick follow-up is not lost
        jobs[cid] = scope.launch {
            if (device != null) relayTurn(cid, device, mention.groupValues[2], text) else runTurn(cid, text, null)
        }
        return "started"
    }

    fun stop(cid: String) {
        jobs[cid]?.cancel()
        takeoverWaiters.remove(cid)?.cancel()
    }

    suspend fun runTurn(cid: String, text: String, remote: RemoteEmit?): Pair<String, String> {
        _running.update { it + cid }
        inboxes.putIfAbsent(cid, Channel(Channel.UNLIMITED))
        var status = "ok"
        var final = ""
        try {
            graph.store.update(cid) { c ->
                c.copy(title = if (c.title == "New chat") text.lines().first().take(60) else c.title,
                    messages = c.messages + Msg(role = "user", content = text))
            }
            final = loop(cid, graph.model(), remote)
        } catch (e: CancellationException) {
            status = "stopped"
            graph.store.append(cid, Msg(role = "assistant", content = "Stopped.", event = true))
        } catch (e: StuckError) {
            status = "error: ${e.message}"
            graph.store.append(cid, Msg(role = "assistant", content = e.message.orEmpty(), event = true, ok = false))
        } catch (e: Exception) {
            status = "error: ${e.message}"
            val what = if (e is LlmError) "Model error: ${e.message}" else "Something went wrong: ${e.message}"
            graph.store.append(cid, Msg(role = "assistant", content = what, event = true, ok = false))
        } finally {
            inboxes.remove(cid)
            jobs.remove(cid)
            handsDone(cid)
            _live.update { it - cid }
            _running.update { it - cid }
        }
        return status to final
    }

    private suspend fun loop(cid: String, model: ChatModel, remote: RemoteEmit?): String {
        var final = ""
        val specs = tools.values.map { it.spec() }
        repeat(40) {
            inboxes[cid]?.let { box ->
                while (true) {
                    val queued = box.tryReceive().getOrNull() ?: break
                    graph.store.append(cid, Msg(role = "user", content = queued))
                }
            }
            val conv = graph.store.get(cid)!!
            val system = systemPrompt(conv.messages.lastOrNull { it.role == "user" }?.content.orEmpty(), conv.origin)
            val context = ContextShaper.shape(conv.messages)
            live(cid) { LiveTurn(remote = it.remote) }
            val onText = { d: String -> live(cid) { it.copy(text = it.text + d) } }
            val onReasoning = { d: String -> live(cid) { it.copy(reasoning = it.reasoning + d) } }
            var reply = model.stream(system, context, specs, onText, onReasoning)
            if (reply.finish == "length" && reply.text.isBlank() && reply.calls.isEmpty()) {
                // the thinking budget ran out before an answer: once more with more room
                reply = model.stream(system, context, specs, onText, onReasoning, maxTokens = (model.maxTokens * 2).coerceAtMost(65536))
            }
            graph.store.append(cid, Msg(role = "assistant", content = reply.text, reasoning = reply.reasoning, toolCalls = reply.calls))
            if (reply.text.isNotBlank()) {
                final = reply.text
                remote?.invoke("text", buildJsonObject { put("delta", reply.text) })
            }
            if (ContextShaper.isStuck(graph.store.get(cid)!!.messages)) throw StuckError()
            if (reply.calls.isEmpty()) {
                if (inboxes[cid]?.isEmpty == false) return@repeat
                return final
            }
            val ctx = ToolCtx(graph, cid, conv.origin)
            var finished = false
            for (call in reply.calls) {
                val tool = tools[call.name]
                val summary = tool?.summary(call.arguments).orEmpty()
                live(cid) { it.copy(text = "", reasoning = "", tools = it.tools + LiveTool(call.id, call.name, summary, "run")) }
                remote?.invoke("tool", buildJsonObject { put("id", call.id); put("name", call.name); put("summary", summary) })
                val out = when {
                    tool == null -> ToolOut("Error: there is no tool named ${call.name}.", ok = false)
                    call.arguments.containsKey("_invalid_json") -> ToolOut("Error: the arguments were not valid JSON. Try again.", ok = false)
                    else -> graph.sentinel.guard(call, tool, ctx, remote)
                }
                val status = if (out.text.startsWith("Refused by the Sentinel")) "refused" else if (out.ok) "ok" else "err"
                graph.store.append(cid, Msg(role = "tool", toolCallId = call.id, name = call.name, content = out.text, ok = out.ok,
                    refused = status == "refused", image = out.shot?.jpegBase64, imageW = out.shot?.width ?: 0, imageH = out.shot?.height ?: 0))
                live(cid) { t -> t.copy(tools = t.tools.map { if (it.id == call.id) it.copy(status = status, output = out.text.take(2000)) else it }) }
                remote?.invoke("tool_result", buildJsonObject { put("id", call.id); put("ok", out.ok); put("summary", out.text.take(300)) })
                if (call.name == "terminate" && out.ok) {
                    final = final.ifBlank { call.arguments.s("summary").orEmpty() }
                    finished = true
                }
            }
            if (finished) return final
        }
        graph.store.append(cid, Msg(role = "assistant", content = "I stopped after 40 steps. Say *continue* to keep going."))
        return final
    }

    private fun systemPrompt(lastUser: String, origin: String): String {
        val cfg = graph.settings.config.value
        val memories = graph.memory.search(lastUser, 6).joinToString("\n") { "- (${it.category}) ${it.content}" }.ifEmpty { "(nothing relevant)" }
        val others = graph.hub.devices.value.filter { it.online && it.id != cfg.deviceId }.joinToString { "${it.name} (${it.kind})" }
            .ifEmpty { if (graph.hub.online.value) "none online" else "not signed in to a relay" }
        val chinese = lastUser.any { it in '㐀'..'鿿' }
        return """You are ${cfg.name}, a personal agent living on the user's Android phone. You do things for them instead of only answering.

# What you remember that may matter now
$memories

# How you work
- Act with your tools. Climb only as high as needed: open_app / deep links and web_fetch first; the screen (phone_screen, phone_act on a 0..999 grid) only when nothing else works. On a screen: look, act once, look again.
- Passwords, payment codes, logins and CAPTCHAs belong to the user: use phone_act with action takeover.
- The Sentinel checks every call. If the user declines, accept it and offer another way; never retry a refused call.
- When you learn a lasting fact about the user, remember it. For anything that needs a computer, use device_task.
- ${if (chinese) "The user writes in Chinese: reply in Chinese (简体中文)." else "Reply in the language of the user's latest message."} Be brief: what you did, what you found.
${if (origin.startsWith("hub:")) "- This task was sent by the user's other device; approvals go to them there.\n" else ""}
# Now
- Time: ${ZonedDateTime.now()}
- This phone: ${Build.MANUFACTURER} ${Build.MODEL}, Android ${Build.VERSION.RELEASE}
- Other devices: $others
"""
    }

    // ---------------------------------------------------------------- devices

    private suspend fun relayTurn(cid: String, device: Device, task: String, original: String) {
        try {
            graph.store.update(cid) { c ->
                c.copy(title = if (c.title == "New chat") original.take(60) else c.title, messages = c.messages + Msg(role = "user", content = original))
            }
            val answer = remoteTask(cid, device, task)
            graph.store.append(cid, Msg(role = "assistant", content = answer, device = device.id))
        } catch (e: CancellationException) {
            graph.store.append(cid, Msg(role = "assistant", content = "Stopped.", event = true))
        } finally {
            graph.approvals.dropMirrors(cid)
            jobs.remove(cid)
            _live.update { it - cid }
            _running.update { it - cid }
        }
    }

    /** Another device does the whole task; its questions are shown here and answered back to it. */
    suspend fun remoteTask(cid: String, device: Device, text: String): String {
        live(cid) { it.copy(remote = it.remote + "${device.name} is on it: $text") }
        val body = graph.hub.call(device.id, "task", buildJsonObject { put("text", text); put("conversation", cid); put("stream", true) }, 3600) { ev ->
            val b = ev["body"] as? JsonObject ?: return@call
            fun str(k: String) = (b[k] as? JsonPrimitive)?.contentOrNull.orEmpty()
            when (str("stage")) {
                "approval" -> {
                    val req = ApprovalRequest(str("approval_id"), cid, str("tool"), str("preview"), str("risk"), str("reason"),
                        runCatching { b["warnings"]!!.jsonArray.map { it.jsonPrimitive.content } }.getOrDefault(emptyList()), str("grant_key"),
                        runCatching { b["scopes"]!!.jsonArray.map { it.jsonPrimitive.content } }.getOrDefault(listOf("once")), device.name)
                    graph.approvals.mirror(req) { allow, scope -> graph.hub.approve(device.id, req.id, allow, scope) }
                }
                "text" -> live(cid) { it.copy(remote = it.remote + str("delta")) }
                "tool" -> live(cid) { it.copy(remote = it.remote + "⚙ ${str("name")} ${str("summary")}") }
                "tool_result" -> live(cid) { it.copy(remote = it.remote + ((if (b["ok"]?.jsonPrimitive?.contentOrNull == "true") "✓ " else "✗ ") + str("summary").take(120))) }
            }
        }
        graph.approvals.dropMirrors(cid)
        val ok = (body["ok"] as? JsonPrimitive)?.contentOrNull == "true"
        return if (ok) "**${device.name}:** ${(body["text"] as? JsonPrimitive)?.contentOrNull.orEmpty()}"
        else "Error from ${device.name}: ${(body["error"] as? JsonPrimitive)?.contentOrNull ?: "failed"}"
    }

    // ---------------------------------------------------------------- hands HUD & takeover

    fun handsStatus(cid: String, label: String) {
        _hands.update { it + (cid to label) }
        runCatching { HandsService.get().showCapsule(label) { stop(cid) } }
    }

    fun handsDone(cid: String) {
        _hands.update { it - cid }
        if (_hands.value.isEmpty()) runCatching { HandsService.get().hideCapsule() }
    }

    suspend fun takeover(cid: String, reason: String): String {
        val wait = CompletableDeferred<String>()
        takeoverWaiters[cid] = wait
        _takeovers.update { it + (cid to reason) }
        try {
            return wait.await()
        } finally {
            takeoverWaiters.remove(cid)
            _takeovers.update { it - cid }
        }
    }

    fun resolveTakeover(cid: String, note: String = "") = takeoverWaiters[cid]?.complete(note) ?: false
}
