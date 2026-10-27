package dev.nemo.android.llm

/** Splits a token stream into answer text and <think>…</think> reasoning, across chunk boundaries. */
class ThinkFilter {
    private var inside = false
    private var buf = ""

    fun feed(chunk: String): List<Pair<Boolean, String>> {
        buf += chunk
        val out = mutableListOf<Pair<Boolean, String>>() // (isReasoning, text)
        while (buf.isNotEmpty()) {
            val tag = if (inside) "</think>" else "<think>"
            val at = buf.indexOf(tag)
            if (at >= 0) {
                if (at > 0) out += inside to buf.substring(0, at)
                buf = buf.substring(at + tag.length)
                inside = !inside
                continue
            }
            var keep = 0
            for (n in minOf(tag.length - 1, buf.length) downTo 1) {
                if (tag.startsWith(buf.takeLast(n))) { keep = n; break }
            }
            val emit = buf.dropLast(keep)
            if (emit.isNotEmpty()) out += inside to emit
            buf = buf.takeLast(keep)
            break
        }
        return out
    }

    fun flush(): List<Pair<Boolean, String>> {
        val rest = buf
        buf = ""
        return if (rest.isEmpty()) emptyList() else listOf(inside to rest)
    }
}
