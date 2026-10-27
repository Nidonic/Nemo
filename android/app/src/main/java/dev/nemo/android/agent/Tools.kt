package dev.nemo.android.agent

import android.app.NotificationManager
import android.content.Intent
import android.net.Uri
import android.os.BatteryManager
import android.os.Build
import androidx.core.app.NotificationCompat
import dev.nemo.android.AppGraph
import dev.nemo.android.NemoApp
import dev.nemo.android.R
import dev.nemo.android.hands.Grid
import dev.nemo.android.hands.HandsService
import dev.nemo.android.hands.HandsUnavailable
import dev.nemo.android.hands.SensitiveWords
import dev.nemo.android.hands.Shot
import dev.nemo.android.llm.ToolSpec
import dev.nemo.android.llm.http
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.delay
import kotlinx.coroutines.withContext
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.buildJsonArray
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.contentOrNull
import kotlinx.serialization.json.intOrNull
import kotlinx.serialization.json.put
import okhttp3.Request

class ToolCtx(val graph: AppGraph, val conversationId: String, val origin: String)

data class ToolOut(val text: String, val ok: Boolean = true, val shot: Shot? = null)

fun JsonObject.s(key: String): String? = (this[key] as? JsonPrimitive)?.contentOrNull
fun JsonObject.i(key: String): Int? = (this[key] as? JsonPrimitive)?.intOrNull ?: s(key)?.toIntOrNull()
fun JsonObject.b(key: String): Boolean = s(key) == "true"

/** A JSON schema object: props are (name, type, description), with `enum` lists allowed via type "enum:a|b". */
fun schema(vararg props: Triple<String, String, String>, required: List<String> = emptyList()) = buildJsonObject {
    put("type", "object")
    put("properties", buildJsonObject {
        for ((name, type, desc) in props) put(name, buildJsonObject {
            if (type.startsWith("enum:")) {
                put("type", "string")
                put("enum", buildJsonArray { type.removePrefix("enum:").split("|").forEach { add(JsonPrimitive(it)) } })
            } else put("type", type)
            if (desc.isNotEmpty()) put("description", desc)
        })
    })
    put("required", buildJsonArray { required.forEach { add(JsonPrimitive(it)) } })
}

abstract class Tool(val name: String, val description: String, val parameters: JsonObject, val risk: Risk) {
    open fun target(args: JsonObject) = ""
    open fun summary(args: JsonObject): String =
        listOf("label", "text", "name", "url", "query", "content", "action").firstNotNullOfOrNull { args.s(it) }.orEmpty().take(160)
    open fun assess(args: JsonObject, ctx: ToolCtx) = Assessment(risk, target(args), summary(args))
    abstract suspend fun run(args: JsonObject, ctx: ToolCtx): ToolOut
    fun spec() = ToolSpec(name, description, parameters)
}

private const val LADDER = "Last resort for apps with no other way in; prefer open_app and web_fetch. "

class ScreenTool : Tool("phone_screen", "${LADDER}Look at the phone's screen: a screenshot plus the visible text and buttons. Actions use a 0..${Grid.MAX} grid.",
    schema(), Risk.SAFE) {
    override suspend fun run(args: JsonObject, ctx: ToolCtx): ToolOut {
        val hands = HandsService.get()
        val shot = hands.screenshot()
        return ToolOut("Screen ${shot.width}x${shot.height}; coordinates are 0..${Grid.MAX}.\n${hands.screenText()}", shot = shot)
    }
}

class ActTool : Tool("phone_act",
    "${LADDER}Do one action on the phone screen: tap, double_tap, long_press, swipe, type (into the focused field), key " +
        "(Back, Home, Recents, Enter, Notifications); takeover hands the phone to the user (passwords, payment codes, CAPTCHAs) and waits; " +
        "finish ends the screen task. Returns a fresh screenshot.",
    schema(
        Triple("action", "enum:" + Grid.ACTIONS.joinToString("|"), ""),
        Triple("x", "integer", "0..999, left to right"), Triple("y", "integer", "0..999, top to bottom"),
        Triple("x2", "integer", "swipe end x"), Triple("y2", "integer", "swipe end y"),
        Triple("label", "string", "What you are acting on, as shown on screen"),
        Triple("text", "string", "for type"), Triple("clear_first", "boolean", ""),
        Triple("key", "enum:" + Grid.KEYS.joinToString("|"), ""),
        Triple("duration_ms", "integer", ""), Triple("reason", "string", "takeover: what the user must do"),
        Triple("summary", "string", "finish: what was done"),
        required = listOf("action"),
    ), Risk.MODERATE) {

    override fun target(args: JsonObject) = args.s("action").orEmpty()

    override fun summary(args: JsonObject): String {
        val what = args.s("label") ?: args.s("text") ?: args.s("key") ?: args.s("reason") ?: ""
        return listOfNotNull(args.s("action"), what.takeIf { it.isNotEmpty() }?.let { "“$it”" }).joinToString(" ")
    }

    override fun assess(args: JsonObject, ctx: ToolCtx): Assessment {
        val a = Assessment(risk, target(args), summary(args))
        SensitiveWords.hit(args.s("label"))?.let { a.warn("The target looks like a “$it” action.") }
        if (args.s("action") in setOf("takeover", "finish")) a.risk = Risk.SAFE
        return a
    }

    override suspend fun run(args: JsonObject, ctx: ToolCtx): ToolOut {
        val action = args.s("action")
        val numbers = listOf("x", "y", "x2", "y2").associateWith { args.i(it) }
        Grid.validate(action, numbers + mapOf("text" to args.s("text"), "key" to args.s("key")))?.let { return ToolOut("Error: $it.", ok = false) }
        val agents = ctx.graph.agents
        when (action) {
            "finish" -> { agents.handsDone(ctx.conversationId); return ToolOut("Screen task finished: ${args.s("summary").orEmpty()}") }
            "takeover" -> {
                agents.handsStatus(ctx.conversationId, "Waiting for you")
                val note = agents.takeover(ctx.conversationId, args.s("reason") ?: "Please take over.")
                return ToolOut("The user took over and is done. $note".trim())
            }
        }
        val hands = HandsService.get()
        agents.handsStatus(ctx.conversationId, summary(args))
        val ms = args.i("duration_ms")?.toLong()
        val done = when (action) {
            "tap" -> hands.tap(numbers["x"]!!, numbers["y"]!!)
            "double_tap" -> hands.doubleTap(numbers["x"]!!, numbers["y"]!!)
            "long_press" -> hands.longPress(numbers["x"]!!, numbers["y"]!!, ms ?: 800)
            "swipe" -> hands.swipe(numbers["x"]!!, numbers["y"]!!, numbers["x2"]!!, numbers["y2"]!!, ms ?: 400)
            "type" -> hands.type(args.s("text")!!, args.b("clear_first"))
            "key" -> hands.key(args.s("key")!!)
            else -> false
        }
        delay(700) // let the screen settle
        val shot = hands.screenshot()
        return ToolOut(if (done) "Did ${summary(args)}." else "Android did not complete ${summary(args)}.", ok = done, shot = shot)
    }
}

private fun launchable(graph: AppGraph): List<Pair<String, String>> {
    val pm = graph.context.packageManager
    val intent = Intent(Intent.ACTION_MAIN).addCategory(Intent.CATEGORY_LAUNCHER)
    return pm.queryIntentActivities(intent, 0).map { it.loadLabel(pm).toString() to it.activityInfo.packageName }.distinct().sortedBy { it.first.lowercase() }
}

class ListAppsTool : Tool("list_apps", "List the apps installed on this phone.", schema(), Risk.SAFE) {
    override suspend fun run(args: JsonObject, ctx: ToolCtx) = ToolOut(launchable(ctx.graph).joinToString("\n") { "${it.first} (${it.second})" })
}

class OpenAppTool : Tool("open_app", "Open an app by name (e.g. WeChat, 支付宝, Maps) or open a URL or deep link (https://…, geo:…, tel:…).",
    schema(Triple("name", "string", "app name or package"), Triple("url", "string", "URL or deep link")), Risk.MODERATE) {

    override fun target(args: JsonObject) = args.s("name") ?: Uri.parse(args.s("url").orEmpty()).host.orEmpty()

    override fun assess(args: JsonObject, ctx: ToolCtx): Assessment {
        val a = super.assess(args, ctx)
        args.s("url")?.let { u ->
            val uri = Uri.parse(u)
            if (uri.scheme in setOf("http", "https")) a.egress = uri.host ?: "*"
            if (uri.scheme in setOf("tel", "sms", "smsto", "mailto")) a.escalate(Risk.SENSITIVE, "Starts a call or message: $u")
        }
        return a
    }

    override suspend fun run(args: JsonObject, ctx: ToolCtx): ToolOut {
        val context = ctx.graph.context
        args.s("url")?.let { url ->
            context.startActivity(Intent(Intent.ACTION_VIEW, Uri.parse(url)).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))
            return ToolOut("Opened $url.")
        }
        val want = args.s("name")?.lowercase() ?: return ToolOut("Error: give a name or a url.", ok = false)
        val apps = launchable(ctx.graph)
        val hit = apps.firstOrNull { it.first.lowercase() == want || it.second == want }
            ?: apps.firstOrNull { it.first.lowercase().contains(want) }
            ?: return ToolOut("Error: no app called “$want”. Use list_apps.", ok = false)
        val launch = context.packageManager.getLaunchIntentForPackage(hit.second)?.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
            ?: return ToolOut("Error: ${hit.first} cannot be opened.", ok = false)
        context.startActivity(launch)
        return ToolOut("Opened ${hit.first}.")
    }
}

class WebFetchTool : Tool("web_fetch", "Fetch a web page (GET) and return its readable text.", schema(Triple("url", "string", ""), required = listOf("url")), Risk.MODERATE) {
    override fun target(args: JsonObject) = Uri.parse(args.s("url").orEmpty()).host.orEmpty()
    override fun assess(args: JsonObject, ctx: ToolCtx) = super.assess(args, ctx).apply { egress = target(args).ifEmpty { "*" } }

    override suspend fun run(args: JsonObject, ctx: ToolCtx): ToolOut = withContext(Dispatchers.IO) {
        val url = args.s("url").orEmpty()
        if (!url.startsWith("http")) return@withContext ToolOut("Error: the URL must start with http(s)://.", ok = false)
        http.newCall(Request.Builder().url(url).header("User-Agent", "Mozilla/5.0 (Linux; Android) Nemo/0.2").build()).execute().use { res ->
            val body = res.body?.string().orEmpty()
            val type = res.header("Content-Type").orEmpty()
            val text = if ("html" in type) htmlToText(body) else body
            ToolOut("${res.code} $url\n\n${text.take(12000)}", ok = res.isSuccessful)
        }
    }
}

fun htmlToText(html: String): String = html
    .replace(Regex("(?is)<(script|style|noscript)[^>]*>.*?</\\1>"), " ")
    .replace(Regex("(?i)<br\\s*/?>|</p>|</div>|</li>|</h[1-6]>"), "\n")
    .replace(Regex("<[^>]+>"), " ")
    .replace("&nbsp;", " ").replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">").replace("&quot;", "\"")
    .replace(Regex("[ \\t]+"), " ").replace(Regex("\\n\\s*\\n+"), "\n\n").trim()

class RememberTool : Tool("remember", "Save one lasting fact about the user.",
    schema(Triple("content", "string", "one self-contained fact"), Triple("category", "enum:preference|identity|work|habit|general", ""), required = listOf("content")), Risk.MODERATE) {
    override suspend fun run(args: JsonObject, ctx: ToolCtx) =
        ctx.graph.memory.add(args.s("content")!!, args.s("category") ?: "general").let { ToolOut("Remembered: ${it.content}") }
}

class RecallTool : Tool("recall", "Search what you remember about the user.", schema(Triple("query", "string", ""), required = listOf("query")), Risk.SAFE) {
    override fun assess(args: JsonObject, ctx: ToolCtx) = super.assess(args, ctx).apply { readsPrivate = true }
    override suspend fun run(args: JsonObject, ctx: ToolCtx): ToolOut {
        val hits = ctx.graph.memory.search(args.s("query").orEmpty())
        return ToolOut(if (hits.isEmpty()) "Nothing remembered about that." else hits.joinToString("\n") { "- (${it.category}) ${it.content}" })
    }
}

class NotifyTool : Tool("notify", "Show the user a notification on this phone (a reminder, a finished result).",
    schema(Triple("title", "string", ""), Triple("text", "string", ""), required = listOf("text")), Risk.SAFE) {
    override suspend fun run(args: JsonObject, ctx: ToolCtx): ToolOut {
        val c = ctx.graph.context
        val n = NotificationCompat.Builder(c, NemoApp.CHANNEL_UPDATES).setSmallIcon(R.mipmap.ic_launcher)
            .setContentTitle(args.s("title") ?: ctx.graph.settings.config.value.name).setContentText(args.s("text"))
            .setStyle(NotificationCompat.BigTextStyle().bigText(args.s("text"))).setAutoCancel(true).build()
        c.getSystemService(NotificationManager::class.java).notify((System.currentTimeMillis() % 100000).toInt(), n)
        return ToolOut("Shown.")
    }
}

class DeviceInfoTool : Tool("device_info", "This phone's model, Android version, battery and time.", schema(), Risk.SAFE) {
    override suspend fun run(args: JsonObject, ctx: ToolCtx): ToolOut {
        val bm = ctx.graph.context.getSystemService(BatteryManager::class.java)
        return ToolOut("${Build.MANUFACTURER} ${Build.MODEL}, Android ${Build.VERSION.RELEASE}; battery ${bm.getIntProperty(BatteryManager.BATTERY_PROPERTY_CAPACITY)}%" +
            (if (bm.isCharging) ", charging" else "") + "; ${java.time.ZonedDateTime.now()}")
    }
}

class DeviceListTool : Tool("device_list", "List the user's other devices (computers, phones) on this account and which are online.", schema(), Risk.SAFE) {
    override suspend fun run(args: JsonObject, ctx: ToolCtx): ToolOut {
        val hub = ctx.graph.hub
        if (!hub.online.value) return ToolOut("Not connected to a relay, so no other devices are reachable.")
        val me = ctx.graph.settings.config.value.deviceId
        return ToolOut(hub.devices.value.joinToString("\n") { d ->
            "- ${d.name} (${d.kind}, ${d.os}) ${if (d.online) "online" else "offline"}${if (d.id == me) " ← this phone" else ""}"
        }.ifEmpty { "No devices." })
    }
}

class DeviceTaskTool : Tool("device_task", "Have another of the user's devices (e.g. their computer) do a whole task with its own agent and tools; " +
    "its approvals come back to this phone. Use for anything that needs a computer: files, shell, desktop apps.",
    schema(Triple("device", "string", "device name from device_list"), Triple("text", "string", "the task"), required = listOf("device", "text")), Risk.MODERATE) {
    override fun target(args: JsonObject) = args.s("device").orEmpty()
    override suspend fun run(args: JsonObject, ctx: ToolCtx): ToolOut {
        val hub = ctx.graph.hub
        val device = hub.find(args.s("device").orEmpty()) ?: return ToolOut("Error: no device called ${args.s("device")}. Use device_list.", ok = false)
        if (!device.online) return ToolOut("Error: ${device.name} is offline.", ok = false)
        return ctx.graph.agents.remoteTask(ctx.conversationId, device, args.s("text").orEmpty()).let { ToolOut(it, ok = !it.startsWith("Error")) }
    }
}

class TerminateTool : Tool("terminate", "End this task now with a one-line summary.", schema(Triple("summary", "string", ""), required = listOf("summary")), Risk.SAFE) {
    override suspend fun run(args: JsonObject, ctx: ToolCtx) = ToolOut("Finished: ${args.s("summary").orEmpty()}")
}

fun phoneTools(): List<Tool> = listOf(
    OpenAppTool(), ListAppsTool(), WebFetchTool(), ScreenTool(), ActTool(), RememberTool(), RecallTool(),
    NotifyTool(), DeviceInfoTool(), DeviceListTool(), DeviceTaskTool(), TerminateTool(),
)
