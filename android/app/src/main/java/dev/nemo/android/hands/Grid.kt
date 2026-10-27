package dev.nemo.android.hands

import kotlin.math.floor

/** The normalised 0..999 grid shared with the desktop: X = ⌊x / 999 × W⌋, Y = ⌊y / 999 × H⌋. */
object Grid {
    const val MAX = 999

    fun toScreen(x: Int, y: Int, width: Int, height: Int): Pair<Int, Int> {
        val cx = x.coerceIn(0, MAX)
        val cy = y.coerceIn(0, MAX)
        val sx = floor(cx.toDouble() / MAX * width).toInt().coerceAtMost(width - 1)
        val sy = floor(cy.toDouble() / MAX * height).toInt().coerceAtMost(height - 1)
        return sx to sy
    }

    val ACTIONS = listOf("tap", "double_tap", "long_press", "swipe", "type", "key", "takeover", "finish")
    val KEYS = listOf("Back", "Home", "Recents", "Enter", "Notifications")

    /** Why an action's arguments are unusable, or null when they are fine. */
    fun validate(action: String?, args: Map<String, Any?>): String? {
        if (action !in ACTIONS) return "action must be one of ${ACTIONS.joinToString()}"
        val need = when (action) {
            "tap", "double_tap", "long_press" -> listOf("x", "y")
            "swipe" -> listOf("x", "y", "x2", "y2")
            "type" -> listOf("text")
            "key" -> listOf("key")
            else -> emptyList()
        }
        val missing = need.filter { args[it] == null }
        if (missing.isNotEmpty()) return "$action needs ${missing.joinToString()}"
        for (k in listOf("x", "y", "x2", "y2")) {
            val v = (args[k] as? Number)?.toInt() ?: continue
            if (v !in 0..MAX) return "$k must be between 0 and $MAX"
        }
        return null
    }
}

/** Labels that mean money moves, something is sent, or something is destroyed. */
object SensitiveWords {
    val WORDS = listOf(
        "确认支付", "立即支付", "转账", "提交订单", "发送", "删除", "购买", "付款",
        "pay", "transfer", "checkout", "delete", "send", "purchase", "place order", "buy now", "confirm payment",
    )

    fun hit(vararg texts: String?): String? {
        for (t in texts) {
            val low = t?.lowercase() ?: continue
            WORDS.firstOrNull { low.contains(it) }?.let { return it }
        }
        return null
    }
}
