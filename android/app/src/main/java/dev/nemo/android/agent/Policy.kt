package dev.nemo.android.agent

/** Risk tiers, the per-call assessment a tool makes, and the Sentinel's decision rules (no Android code here). */
enum class Risk { SAFE, MODERATE, SENSITIVE;
    fun max(o: Risk) = if (o.ordinal > ordinal) o else this
}

enum class Mode { ASK, STRICT, AUTO }

enum class Decision { ALLOW, ASK, DENY }

data class Assessment(
    var risk: Risk,
    val target: String = "",
    val summary: String = "",
    val warnings: MutableList<String> = mutableListOf(),
    val notes: MutableList<String> = mutableListOf(),
    /** host data may go to; "*" = anywhere */
    var egress: String? = null,
    var readsPrivate: Boolean = false,
) {
    fun warn(text: String) {
        if (text !in warnings) warnings += text
        risk = Risk.SENSITIVE
    }

    fun escalate(to: Risk, note: String) {
        risk = risk.max(to)
        if (note !in notes) notes += note
    }
}

data class Verdict(val decision: Decision, val reason: String, val mustAsk: Boolean = false, val stage: String = "")

object Policy {
    val DEFAULT_ALLOWLIST = listOf("api.github.com", "github.com", "duckduckgo.com", "wikipedia.org")

    fun hostAllowed(host: String, allowlist: List<String>): Boolean {
        val h = host.lowercase().trim('.')
        return allowlist.any { raw ->
            val e = raw.lowercase().trim('.').removePrefix("*.")
            h == e || h.endsWith(".$e")
        }
    }

    /** mode matrix → taint rule → warning override. */
    fun evaluate(mode: Mode, a: Assessment, tainted: Boolean, allowlist: List<String> = DEFAULT_ALLOWLIST, denied: Boolean = false): Verdict {
        if (denied) return Verdict(Decision.DENY, "This tool is turned off.", stage = "deny_list")
        val decision = when (mode) {
            Mode.ASK -> if (a.risk == Risk.SENSITIVE) Decision.ASK else Decision.ALLOW
            Mode.STRICT -> if (a.risk == Risk.SAFE) Decision.ALLOW else Decision.ASK
            Mode.AUTO -> Decision.ALLOW
        }
        val egress = a.egress
        if (tainted && egress != null && (egress == "*" || !hostAllowed(egress, allowlist))) {
            val where = if (egress == "*") "an arbitrary destination" else egress
            return Verdict(Decision.ASK, "This conversation has read private data, and this can send data to $where.", mustAsk = true, stage = "taint")
        }
        if (a.warnings.isNotEmpty()) return Verdict(Decision.ASK, a.warnings.joinToString(" "), mustAsk = true, stage = "warning")
        val reason = if (decision == Decision.ASK) a.notes.joinToString(" ").ifBlank { "${a.risk.name.lowercase().replaceFirstChar { it.uppercase() }} action." }
        else "${a.risk.name.lowercase()} action allowed"
        return Verdict(decision, reason, stage = "mode")
    }
}
