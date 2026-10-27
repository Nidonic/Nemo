package dev.nemo.android.llm

import dev.nemo.android.data.Msg
import dev.nemo.android.data.ToolCall
import dev.nemo.android.data.json
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.ensureActive
import kotlinx.coroutines.withContext
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonNull
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.buildJsonArray
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.contentOrNull
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import kotlinx.serialization.json.put
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import java.util.concurrent.TimeUnit

class LlmError(message: String) : Exception(message)

data class ToolSpec(val name: String, val description: String, val parameters: JsonObject)

data class Reply(val text: String, val reasoning: String, val calls: List<ToolCall>, val finish: String)

interface ChatModel {
    val maxTokens: Int
    suspend fun stream(system: String, messages: List<Msg>, tools: List<ToolSpec>, onText: (String) -> Unit,
                       onReasoning: (String) -> Unit, maxTokens: Int? = null): Reply
}

val http: OkHttpClient = OkHttpClient.Builder()
    .connectTimeout(20, TimeUnit.SECONDS)
    .readTimeout(300, TimeUnit.SECONDS)
    .pingInterval(25, TimeUnit.SECONDS)
    .build()

private val JSON_TYPE = "application/json".toMediaType()

fun parseArgs(raw: String): JsonObject =
    if (raw.isBlank()) JsonObject(emptyMap())
    else runCatching { json.parseToJsonElement(raw).jsonObject }.getOrElse { buildJsonObject { put("_invalid_json", raw) } }

/** POST a streaming request and hand each SSE `data:` payload to [onData]; cancels the call with the coroutine. */
private suspend fun sse(url: String, body: JsonObject, headers: Map<String, String>, onData: (String) -> Boolean) =
    withContext(Dispatchers.IO) {
        val req = Request.Builder().url(url).post(body.toString().toRequestBody(JSON_TYPE)).apply {
            headers.forEach { (k, v) -> header(k, v) }
        }.build()
        val call = http.newCall(req)
        val job = coroutineContext[Job]
        val handle = job?.invokeOnCompletion { if (it != null) call.cancel() }
        try {
            call.execute().use { res ->
                if (!res.isSuccessful) {
                    val text = res.body?.string().orEmpty()
                    val msg = runCatching {
                        val err = json.parseToJsonElement(text).jsonObject["error"]
                        (err as? JsonObject)?.get("message")?.jsonPrimitive?.contentOrNull ?: err.toString()
                    }.getOrDefault(text)
                    throw LlmError("${res.code}: ${msg.take(400)}")
                }
                val source = res.body!!.source()
                while (true) {
                    coroutineContext.ensureActive()
                    val line = source.readUtf8Line() ?: break
                    if (!line.startsWith("data:")) continue
                    if (!onData(line.removePrefix("data:").trim())) break
                }
            }
        } finally {
            handle?.dispose()
        }
    }

private fun JsonElement?.str(): String? = (this as? JsonPrimitive)?.contentOrNull

class OpenAIChat(baseUrl: String, private val key: String, private val model: String, override val maxTokens: Int = 8192) : ChatModel {
    private val url = baseUrl.trimEnd('/') + "/chat/completions"

    fun convert(messages: List<Msg>): JsonArray = buildJsonArray {
        for (m in messages) when (m.role) {
            "assistant" -> {
                if (m.toolCalls.isEmpty() && m.content.isBlank()) continue
                add(buildJsonObject {
                    put("role", "assistant")
                    put("content", if (m.content.isBlank()) JsonNull else JsonPrimitive(m.content))
                    if (m.toolCalls.isNotEmpty()) put("tool_calls", buildJsonArray {
                        m.toolCalls.forEach { c ->
                            add(buildJsonObject {
                                put("id", c.id); put("type", "function")
                                put("function", buildJsonObject { put("name", c.name); put("arguments", c.arguments.toString()) })
                            })
                        }
                    })
                })
            }
            "tool" -> {
                add(buildJsonObject { put("role", "tool"); put("tool_call_id", m.toolCallId ?: ""); put("content", m.content.ifBlank { "(empty)" }) })
                if (!m.image.isNullOrEmpty()) add(userWithImage("(screenshot from ${m.name})", m.image))
            }
            else -> add(if (!m.image.isNullOrEmpty()) userWithImage(m.content, m.image) else buildJsonObject { put("role", "user"); put("content", m.content) })
        }
    }

    private fun userWithImage(text: String, image: String) = buildJsonObject {
        put("role", "user")
        put("content", buildJsonArray {
            add(buildJsonObject { put("type", "text"); put("text", text.ifBlank { "(image)" }) })
            add(buildJsonObject { put("type", "image_url"); put("image_url", buildJsonObject { put("url", "data:image/jpeg;base64,$image") }) })
        })
    }

    override suspend fun stream(system: String, messages: List<Msg>, tools: List<ToolSpec>, onText: (String) -> Unit,
                                onReasoning: (String) -> Unit, maxTokens: Int?): Reply {
        val body = buildJsonObject {
            put("model", model)
            put("stream", true)
            put(if (url.contains("api.openai.com")) "max_completion_tokens" else "max_tokens", maxTokens ?: this@OpenAIChat.maxTokens)
            put("messages", buildJsonArray {
                add(buildJsonObject { put("role", "system"); put("content", system) })
                convert(messages).forEach { add(it) }
            })
            if (tools.isNotEmpty()) put("tools", buildJsonArray {
                tools.forEach { t ->
                    add(buildJsonObject {
                        put("type", "function")
                        put("function", buildJsonObject { put("name", t.name); put("description", t.description); put("parameters", t.parameters) })
                    })
                }
            })
        }
        val think = ThinkFilter()
        val text = StringBuilder()
        val reasoning = StringBuilder()
        val calls = sortedMapOf<Int, Array<String>>() // index → [id, name, args]
        var finish = "stop"
        fun route(isReasoning: Boolean, piece: String) {
            if (isReasoning) { reasoning.append(piece); onReasoning(piece) } else { text.append(piece); onText(piece) }
        }
        sse(url, body, if (key.isNotBlank()) mapOf("Authorization" to "Bearer $key") else emptyMap()) { data ->
            if (data == "[DONE]") return@sse false
            val chunk = runCatching { json.parseToJsonElement(data).jsonObject }.getOrNull() ?: return@sse true
            chunk["error"]?.let { throw LlmError(it.toString().take(400)) }
            for (choice in (chunk["choices"] as? JsonArray).orEmpty()) {
                val c = choice.jsonObject
                val delta = c["delta"] as? JsonObject ?: JsonObject(emptyMap())
                (delta["reasoning_content"].str() ?: delta["reasoning"].str())?.takeIf { it.isNotEmpty() }?.let { route(true, it) }
                delta["content"].str()?.takeIf { it.isNotEmpty() }?.let { s -> think.feed(s).forEach { (r, p) -> route(r, p) } }
                for (tc in (delta["tool_calls"] as? JsonArray).orEmpty()) {
                    val o = tc.jsonObject
                    val slot = calls.getOrPut(o["index"].str()?.toIntOrNull() ?: 0) { arrayOf("", "", "") }
                    o["id"].str()?.let { if (it.isNotEmpty()) slot[0] = it }
                    val fn = o["function"] as? JsonObject
                    fn?.get("name").str()?.let { if (slot[1].isEmpty()) slot[1] = it }
                    fn?.get("arguments").str()?.let { slot[2] += it }
                }
                c["finish_reason"].str()?.let { finish = it }
            }
            true
        }
        think.flush().forEach { (r, p) -> route(r, p) }
        val toolCalls = calls.entries.filter { it.value[1].isNotEmpty() }.map { (i, s) ->
            ToolCall(id = s[0].ifEmpty { "call_$i" }, name = s[1], arguments = parseArgs(s[2]))
        }
        return Reply(text.toString(), reasoning.toString(), toolCalls, finish)
    }
}

class AnthropicChat(baseUrl: String, private val key: String, private val model: String, override val maxTokens: Int = 8192) : ChatModel {
    private val url = baseUrl.trimEnd('/') + "/v1/messages"

    fun convert(messages: List<Msg>): JsonArray {
        val out = mutableListOf<Pair<String, MutableList<JsonObject>>>()
        fun push(role: String, blocks: List<JsonObject>) {
            if (out.isNotEmpty() && out.last().first == role) out.last().second += blocks else out += role to blocks.toMutableList()
        }
        fun image(data: String) = buildJsonObject {
            put("type", "image")
            put("source", buildJsonObject { put("type", "base64"); put("media_type", "image/jpeg"); put("data", data) })
        }
        for (m in messages) when (m.role) {
            "assistant" -> {
                val blocks = mutableListOf<JsonObject>()
                if (m.content.isNotBlank()) blocks += buildJsonObject { put("type", "text"); put("text", m.content) }
                m.toolCalls.forEach { c -> blocks += buildJsonObject { put("type", "tool_use"); put("id", c.id); put("name", c.name); put("input", c.arguments) } }
                if (blocks.isNotEmpty()) push("assistant", blocks)
            }
            "tool" -> push("user", listOf(buildJsonObject {
                put("type", "tool_result"); put("tool_use_id", m.toolCallId ?: "")
                if (m.ok == false) put("is_error", true)
                put("content", buildJsonArray {
                    add(buildJsonObject { put("type", "text"); put("text", m.content.ifBlank { "(empty)" }) })
                    if (!m.image.isNullOrEmpty()) add(image(m.image))
                })
            }))
            else -> if (m.content.isNotBlank() || !m.image.isNullOrEmpty()) push("user", buildList {
                if (!m.image.isNullOrEmpty()) add(image(m.image))
                add(buildJsonObject { put("type", "text"); put("text", m.content.ifBlank { "(image)" }) })
            })
        }
        return buildJsonArray { out.forEach { (role, blocks) -> add(buildJsonObject { put("role", role); put("content", JsonArray(blocks)) }) } }
    }

    override suspend fun stream(system: String, messages: List<Msg>, tools: List<ToolSpec>, onText: (String) -> Unit,
                                onReasoning: (String) -> Unit, maxTokens: Int?): Reply {
        val body = buildJsonObject {
            put("model", model); put("stream", true); put("system", system)
            put("max_tokens", maxTokens ?: this@AnthropicChat.maxTokens)
            put("messages", convert(messages))
            if (tools.isNotEmpty()) put("tools", buildJsonArray {
                tools.forEach { t -> add(buildJsonObject { put("name", t.name); put("description", t.description); put("input_schema", t.parameters) }) }
            })
        }
        val think = ThinkFilter()
        val text = StringBuilder()
        val reasoning = StringBuilder()
        val blocks = sortedMapOf<Int, Array<String>>()
        var finish = "stop"
        fun route(isReasoning: Boolean, piece: String) {
            if (isReasoning) { reasoning.append(piece); onReasoning(piece) } else { text.append(piece); onText(piece) }
        }
        sse(url, body, mapOf("x-api-key" to key, "anthropic-version" to "2023-06-01")) { data ->
            val ev = runCatching { json.parseToJsonElement(data).jsonObject }.getOrNull() ?: return@sse true
            when (ev["type"].str()) {
                "error" -> throw LlmError(ev["error"].toString().take(400))
                "content_block_start" -> {
                    val b = ev["content_block"]?.jsonObject
                    if (b?.get("type").str() == "tool_use") blocks[ev["index"].str()!!.toInt()] = arrayOf(b!!["id"].str()!!, b["name"].str()!!, "")
                }
                "content_block_delta" -> {
                    val d = ev["delta"]!!.jsonObject
                    when (d["type"].str()) {
                        "text_delta" -> d["text"].str()?.let { s -> think.feed(s).forEach { (r, p) -> route(r, p) } }
                        "thinking_delta" -> d["thinking"].str()?.let { route(true, it) }
                        "input_json_delta" -> blocks[ev["index"].str()!!.toInt()]?.let { it[2] += d["partial_json"].str().orEmpty() }
                    }
                }
                "message_delta" -> (ev["delta"] as? JsonObject)?.get("stop_reason").str()?.let {
                    finish = when (it) { "max_tokens" -> "length"; "tool_use" -> "tool_calls"; else -> "stop" }
                }
                "message_stop" -> return@sse false
            }
            true
        }
        think.flush().forEach { (r, p) -> route(r, p) }
        return Reply(text.toString(), reasoning.toString(), blocks.values.map { ToolCall(it[0], it[1], parseArgs(it[2])) }, finish)
    }
}

/** For tests and offline demos: plays back scripted replies. */
class ScriptedModel(private val script: MutableList<Reply>) : ChatModel {
    override val maxTokens = 8192
    val seen = mutableListOf<List<Msg>>()
    override suspend fun stream(system: String, messages: List<Msg>, tools: List<ToolSpec>, onText: (String) -> Unit,
                                onReasoning: (String) -> Unit, maxTokens: Int?): Reply {
        seen += messages
        val r = if (script.isEmpty()) Reply("(done)", "", emptyList(), "stop") else script.removeAt(0)
        if (r.text.isNotEmpty()) onText(r.text)
        return r
    }
}
