package dev.nemo.android.agent

import dev.nemo.android.data.Msg
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.buildJsonArray

/** What the model sees of a conversation: no notices, every call answered, pruned at user turns, old screenshots as stubs. */
object ContextShaper {
    fun repair(messages: List<Msg>): List<Msg> {
        val answered = messages.filter { it.role == "tool" }.mapNotNull { it.toolCallId }.toSet()
        val seen = mutableSetOf<String>()
        val out = mutableListOf<Msg>()
        for (m in messages) {
            if (m.event || m.role !in setOf("user", "assistant", "tool")) continue
            if (m.role == "tool" && m.toolCallId !in seen) continue
            out += m
            for (c in m.toolCalls) {
                seen += c.id
                if (c.id !in answered) out += Msg(role = "tool", toolCallId = c.id, name = c.name, ok = false,
                    content = "Cancelled: the run stopped before this call finished.")
            }
        }
        return out
    }

    /** Keep the newest [max]; the cut moves forward to a user message, or back if there is none after it. */
    fun prune(messages: List<Msg>, max: Int): List<Msg> {
        if (messages.size <= max) return messages
        val cut = messages.size - max
        val forward = (cut until messages.size).firstOrNull { messages[it].role == "user" }
        if (forward != null) return messages.subList(forward, messages.size)
        val back = (cut downTo 0).firstOrNull { messages[it].role == "user" } ?: 0
        return messages.subList(back, messages.size)
    }

    fun compressImages(messages: List<Msg>, keep: Int): List<Msg> {
        var seen = 0
        return messages.asReversed().map { m ->
            if (m.image.isNullOrEmpty()) m
            else if (++seen <= keep) m
            else m.copy(image = null, content = "${m.content}\n[Screenshot: ${m.imageW}x${m.imageH}]".trim())
        }.asReversed()
    }

    fun shape(messages: List<Msg>, maxMessages: Int = 40, maxImages: Int = 3) =
        compressImages(repair(prune(repair(messages), maxMessages)), maxImages)

    private fun signature(m: Msg): String = buildJsonArray {
        add(JsonPrimitive(m.content))
        m.toolCalls.forEach { add(JsonPrimitive(it.name)); add(it.arguments) }
    }.toString()

    /** The last [window] assistant messages since the user last spoke are identical. */
    fun isStuck(messages: List<Msg>, window: Int = 3): Boolean {
        val recent = mutableListOf<String>()
        for (m in messages.asReversed()) {
            if (m.role == "user") break
            if (m.role == "assistant" && !m.event) {
                recent += signature(m)
                if (recent.size == window) break
            }
        }
        return recent.size == window && recent.toSet().size == 1
    }
}

/** Words + CJK character bigrams, rare-word IDF scoring: IDF(w) = ln(1 + (N − n + 0.5)/(n + 0.5)). */
object Lexical {
    private val word = Regex("[^\\W_\\u3400-\\u9fff]+")
    private val cjk = Regex("[\\u3400-\\u9fff]+")
    private val stop = "a an and are as at be but by for from has have i in is it its me my of on or so that the this to was were will with you your".split(" ").toSet()

    fun tokens(text: String): Set<String> {
        val out = word.findAll(text).map { it.value.lowercase() }.filter { it !in stop }.toMutableSet()
        for (run in cjk.findAll(text).map { it.value }) {
            if (run.length == 1) out += run else for (i in 0 until run.length - 1) out += run.substring(i, i + 2)
        }
        return out
    }

    fun <T> rank(query: String, items: List<T>, text: (T) -> String): List<T> {
        val docs = items.map { tokens(text(it)) }
        val df = HashMap<String, Int>()
        docs.forEach { d -> d.forEach { df[it] = (df[it] ?: 0) + 1 } }
        val n = docs.size
        val q = tokens(query)
        return items.zip(docs).map { (item, d) ->
            item to (q intersect d).sumOf { w -> val k = df[w] ?: 0; kotlin.math.ln(1 + (n - k + 0.5) / (k + 0.5)) }
        }.filter { it.second > 0 }.sortedByDescending { it.second }.map { it.first }
    }
}
