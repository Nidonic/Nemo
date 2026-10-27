package dev.nemo.android

import dev.nemo.android.agent.Assessment
import dev.nemo.android.agent.ContextShaper
import dev.nemo.android.agent.Decision
import dev.nemo.android.agent.Lexical
import dev.nemo.android.agent.Mode
import dev.nemo.android.agent.Policy
import dev.nemo.android.agent.Risk
import dev.nemo.android.data.Msg
import dev.nemo.android.data.ToolCall
import dev.nemo.android.hands.Grid
import dev.nemo.android.hands.SensitiveWords
import dev.nemo.android.llm.ThinkFilter
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.buildJsonObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class GridTest {
    @Test fun corners_and_centre() {
        assertEquals(0 to 0, Grid.toScreen(0, 0, 1080, 2400))
        assertEquals(1079 to 2399, Grid.toScreen(999, 999, 1080, 2400))
        assertEquals(540 to 1200, Grid.toScreen(500, 500, 1080, 2400))
        assertEquals(0 to 2399, Grid.toScreen(-5, 5000, 1080, 2400)) // clamped
    }

    @Test fun validation() {
        assertNull(Grid.validate("tap", mapOf("x" to 1, "y" to 2)))
        assertTrue(Grid.validate("swipe", mapOf("x" to 1, "y" to 2))!!.contains("needs"))
        assertTrue(Grid.validate("tap", mapOf("x" to 1000, "y" to 2))!!.contains("between"))
        assertNotNull(Grid.validate("fly", emptyMap()))
    }

    @Test fun sensitive_words() {
        listOf("确认支付", "转账", "提交订单", "Send", "Checkout", "Pay now").forEach { assertNotNull(it, SensitiveWords.hit(it)) }
        listOf("Search", "返回", null).forEach { assertNull(SensitiveWords.hit(it)) }
    }
}

class PolicyTest {
    private fun a(risk: Risk, egress: String? = null, warn: Boolean = false) =
        Assessment(risk, egress = egress).apply { if (warn) warn("dangerous") }

    @Test fun mode_matrix() {
        assertEquals(Decision.ALLOW, Policy.evaluate(Mode.ASK, a(Risk.MODERATE), false).decision)
        assertEquals(Decision.ASK, Policy.evaluate(Mode.ASK, a(Risk.SENSITIVE), false).decision)
        assertEquals(Decision.ASK, Policy.evaluate(Mode.STRICT, a(Risk.MODERATE), false).decision)
        assertEquals(Decision.ALLOW, Policy.evaluate(Mode.STRICT, a(Risk.SAFE), false).decision)
        assertEquals(Decision.ALLOW, Policy.evaluate(Mode.AUTO, a(Risk.SENSITIVE), false).decision)
    }

    @Test fun taint_forces_ask_even_in_auto() {
        val v = Policy.evaluate(Mode.AUTO, a(Risk.MODERATE, egress = "evil.example"), tainted = true)
        assertEquals(Decision.ASK, v.decision)
        assertTrue(v.mustAsk)
        assertEquals(Decision.ALLOW, Policy.evaluate(Mode.AUTO, a(Risk.MODERATE, egress = "api.github.com"), tainted = true).decision)
        assertEquals(Decision.ALLOW, Policy.evaluate(Mode.AUTO, a(Risk.MODERATE, egress = "evil.example"), tainted = false).decision)
    }

    @Test fun warnings_always_ask() {
        val v = Policy.evaluate(Mode.AUTO, a(Risk.MODERATE, warn = true), false)
        assertEquals(Decision.ASK, v.decision)
        assertTrue(v.mustAsk)
    }

    @Test fun host_allowlist() {
        assertTrue(Policy.hostAllowed("api.github.com", listOf("github.com")))
        assertFalse(Policy.hostAllowed("evilgithub.com", listOf("github.com")))
        assertTrue(Policy.hostAllowed("zh.wikipedia.org", listOf("*.wikipedia.org")))
    }
}

class ThinkFilterTest {
    @Test fun splits_across_chunks() {
        val f = ThinkFilter()
        val out = mutableListOf<Pair<Boolean, String>>()
        listOf("Hi <thi", "nk>secret", " plan</th", "ink> there", "<").forEach { out += f.feed(it) }
        out += f.flush()
        assertEquals("Hi  there<", out.filter { !it.first }.joinToString("") { it.second })
        assertEquals("secret plan", out.filter { it.first }.joinToString("") { it.second })
    }
}

class ContextTest {
    private fun exchange(i: Int) = listOf(
        Msg(role = "user", content = "q$i"),
        Msg(role = "assistant", toolCalls = listOf(ToolCall("c$i", "list_apps"))),
        Msg(role = "tool", toolCallId = "c$i", content = "r"),
        Msg(role = "assistant", content = "a$i"),
    )

    @Test fun prune_never_splits_a_tool_exchange() {
        val msgs = (0 until 10).flatMap(::exchange)
        for (budget in 1..40) {
            val kept = ContextShaper.prune(msgs, budget)
            assertEquals("user", kept.first().role)
            val results = kept.filter { it.role == "tool" }.map { it.toolCallId }.toSet()
            val calls = kept.flatMap { it.toolCalls }.map { it.id }.toSet()
            assertEquals(calls, results)
        }
    }

    @Test fun repair_answers_stopped_calls_and_drops_notices() {
        val msgs = listOf(
            Msg(role = "user", content = "go"),
            Msg(role = "assistant", toolCalls = listOf(ToolCall("z", "phone_screen"))),
            Msg(role = "assistant", content = "Stopped.", event = true),
            Msg(role = "user", content = "again"),
        )
        assertEquals(listOf("user", "assistant", "tool", "user"), ContextShaper.repair(msgs).map { it.role })
    }

    @Test fun only_newest_three_screenshots_stay() {
        val msgs = (1..5).map { Msg(role = "tool", toolCallId = "$it", image = "AAAA", imageW = 1080, imageH = 2400) }
        val out = ContextShaper.compressImages(msgs, 3)
        assertEquals(listOf(false, false, true, true, true), out.map { it.image != null })
        assertTrue(out[0].content.contains("[Screenshot: 1080x2400]"))
    }

    @Test fun stuck_after_three_identical_steps() {
        fun step() = Msg(role = "assistant", toolCalls = listOf(ToolCall(name = "phone_act", arguments = buildJsonObject { put("action", JsonPrimitive("tap")) })))
        assertTrue(ContextShaper.isStuck(listOf(Msg(role = "user", content = "go"), step(), step(), step())))
        assertFalse(ContextShaper.isStuck(listOf(step(), step(), Msg(role = "user", content = "again"), step())))
    }
}

class LexicalTest {
    @Test fun words_and_cjk_bigrams() {
        assertEquals(setOf("我喜", "喜欢", "欢咖", "咖啡"), Lexical.tokens("我喜欢咖啡"))
        assertTrue("coffee" in Lexical.tokens("I like coffee"))
    }

    @Test fun rare_words_rank_first() {
        val facts = listOf("The user likes coffee", "The user works at Zhejiang University", "The user lives in 杭州")
        assertEquals("The user works at Zhejiang University", Lexical.rank("which university", facts) { it }.first())
        assertEquals("The user lives in 杭州", Lexical.rank("杭州天气", facts) { it }.first())
    }
}
