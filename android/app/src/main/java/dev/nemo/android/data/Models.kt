package dev.nemo.android.data

import kotlinx.serialization.Serializable
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonObject
import java.util.UUID

val json = Json { ignoreUnknownKeys = true; encodeDefaults = true; explicitNulls = false }

fun newId(prefix: String) = prefix + UUID.randomUUID().toString().replace("-", "").take(12)
fun nowSec() = System.currentTimeMillis() / 1000.0

@Serializable
data class ToolCall(val id: String = newId("call_"), val name: String, val arguments: JsonObject = JsonObject(emptyMap()))

@Serializable
data class Msg(
    val id: String = newId("m_"),
    val role: String, // user | assistant | tool
    val content: String = "",
    val reasoning: String = "",
    val toolCalls: List<ToolCall> = emptyList(),
    val toolCallId: String? = null,
    val name: String? = null,
    val ok: Boolean? = null,
    val refused: Boolean = false,
    /** base64 JPEG; emptied in what the model sees once older than the newest three */
    val image: String? = null,
    val imageW: Int = 0,
    val imageH: Int = 0,
    /** a notice (stopped, errors) shown in the chat but never sent to the model */
    val event: Boolean = false,
    val device: String = "",
    val createdAt: Double = nowSec(),
)

@Serializable
data class Conversation(
    val id: String = newId("t_"),
    val title: String = "New chat",
    val created: Double = nowSec(),
    val updated: Double = nowSec(),
    val messages: List<Msg> = emptyList(),
    val origin: String = "local", // or "hub:<device id>"
)

@Serializable
data class Fact(val id: String = newId("mem_"), val content: String, val category: String = "general", val created: Double = nowSec())
