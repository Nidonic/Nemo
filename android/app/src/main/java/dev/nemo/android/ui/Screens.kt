package dev.nemo.android.ui

import android.content.Intent
import android.net.Uri
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.selection.selectable
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.Chat
import androidx.compose.material.icons.filled.Add
import androidx.compose.material.icons.filled.Delete
import androidx.compose.material.icons.filled.Devices
import androidx.compose.material.icons.filled.Laptop
import androidx.compose.material.icons.filled.Settings
import androidx.compose.material.icons.filled.Smartphone
import androidx.compose.material.icons.filled.Sync
import androidx.compose.material.icons.filled.AutoAwesome
import androidx.compose.material3.Badge
import androidx.compose.material3.BadgedBox
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.ExtendedFloatingActionButton
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.NavigationBar
import androidx.compose.material3.NavigationBarItem
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.RadioButton
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.TopAppBar
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import dev.nemo.android.AppGraph
import dev.nemo.android.agent.Mode
import dev.nemo.android.data.PROVIDERS
import dev.nemo.android.hands.HandsService
import dev.nemo.android.hub.Account
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.put
import java.text.DateFormat
import java.util.Date

enum class Tab(val label: String) { CHATS("Chats"), DEVICES("Devices"), MEMORY("Memory"), SETTINGS("Settings") }

@Composable
fun NemoRoot(graph: AppGraph) = NemoTheme {
    var tab by rememberSaveable { mutableStateOf(Tab.CHATS) }
    var open by rememberSaveable { mutableStateOf<String?>(null) }
    val approvals by graph.approvals.pending.collectAsState()
    val config by graph.settings.config.collectAsState()
    // first run: no model yet → Settings
    LaunchedEffect(Unit) { if (!config.ready && graph.store.list.value.isEmpty()) tab = Tab.SETTINGS }

    val current = open
    if (current != null && tab == Tab.CHATS) {
        Surface(Modifier.fillMaxSize()) { ChatScreen(graph, current) { open = null } }
        return@NemoTheme
    }
    Scaffold(bottomBar = {
        NavigationBar {
            Tab.entries.forEach { t ->
                NavigationBarItem(selected = tab == t, onClick = { tab = t }, label = { Text(t.label) }, icon = {
                    val icon = when (t) {
                        Tab.CHATS -> Icons.AutoMirrored.Filled.Chat
                        Tab.DEVICES -> Icons.Default.Devices
                        Tab.MEMORY -> Icons.Default.AutoAwesome
                        Tab.SETTINGS -> Icons.Default.Settings
                    }
                    BadgedBox(badge = { if (t == Tab.CHATS && approvals.isNotEmpty()) Badge() }) { Icon(icon, null) }
                })
            }
        }
    }) { pad ->
        Column(Modifier.padding(pad).fillMaxSize()) {
            when (tab) {
                Tab.CHATS -> ChatsScreen(graph) { open = it }
                Tab.DEVICES -> DevicesScreen(graph)
                Tab.MEMORY -> MemoryScreen(graph)
                Tab.SETTINGS -> SettingsScreen(graph)
            }
        }
    }
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun Header(title: String, subtitle: String? = null) {
    TopAppBar(title = {
        Column {
            Text(title, fontWeight = FontWeight.SemiBold)
            if (subtitle != null) Text(subtitle, fontSize = 12.sp, color = MaterialTheme.colorScheme.onSurfaceVariant, maxLines = 1, overflow = TextOverflow.Ellipsis)
        }
    })
}

@Composable
fun ChatsScreen(graph: AppGraph, onOpen: (String) -> Unit) {
    val convs by graph.store.list.collectAsState()
    val running by graph.agents.running.collectAsState()
    val approvals by graph.approvals.pending.collectAsState()
    val config by graph.settings.config.collectAsState()
    val waiting = approvals.map { it.conversationId }.toSet()
    Scaffold(topBar = { Header(config.name, if (config.ready) (if (config.provider == "relay") "relay allowance" else config.model) else "no model yet — see Settings") },
        floatingActionButton = {
            ExtendedFloatingActionButton(onClick = { onOpen(graph.store.create().id) }, icon = { Icon(Icons.Default.Add, null) }, text = { Text("New chat") })
        }) { pad ->
        LazyColumn(Modifier.padding(pad).fillMaxSize(), contentPadding = PaddingValues(bottom = 96.dp)) {
            if (convs.isEmpty()) item {
                Text("Hi, I'm ${config.name}. I do things on this phone and reach your other devices; I ask before anything you couldn't undo.",
                    Modifier.padding(24.dp), color = MaterialTheme.colorScheme.onSurfaceVariant)
            }
            items(convs, key = { it.id }) { c ->
                Row(Modifier.fillMaxWidth().clickable { onOpen(c.id) }.padding(horizontal = 16.dp, vertical = 12.dp), verticalAlignment = Alignment.CenterVertically) {
                    Column(Modifier.weight(1f)) {
                        Text(c.title, maxLines = 1, overflow = TextOverflow.Ellipsis, fontWeight = FontWeight.Medium)
                        Text((if (c.origin.startsWith("hub:")) "from another device · " else "") +
                            DateFormat.getDateTimeInstance(DateFormat.SHORT, DateFormat.SHORT).format(Date((c.updated * 1000).toLong())),
                            fontSize = 12.sp, color = MaterialTheme.colorScheme.onSurfaceVariant)
                    }
                    if (c.id in waiting) Text("needs you", color = Warn, fontSize = 12.sp, modifier = Modifier.padding(end = 8.dp))
                    else if (c.id in running) Text("working", color = MaterialTheme.colorScheme.primary, fontSize = 12.sp, modifier = Modifier.padding(end = 8.dp))
                    IconButton(onClick = { graph.agents.stop(c.id); graph.store.delete(c.id) }) { Icon(Icons.Default.Delete, "Delete", tint = MaterialTheme.colorScheme.onSurfaceVariant) }
                }
                HorizontalDivider()
            }
        }
    }
}

@Composable
fun DevicesScreen(graph: AppGraph) {
    val context = LocalContext.current
    val config by graph.settings.config.collectAsState()
    val handsOn by HandsService.enabled.collectAsState()
    val online by graph.hub.online.collectAsState()
    val devices by graph.hub.devices.collectAsState()
    val error by graph.hub.error.collectAsState()
    val scope = rememberCoroutineScope()
    var relay by remember { mutableStateOf(config.relayUrl) }
    var identifier by remember { mutableStateOf("") }
    var code by remember { mutableStateOf("") }
    var sent by remember { mutableStateOf(false) }
    var status by remember { mutableStateOf("") }
    var info by remember { mutableStateOf("") }

    Column(Modifier.fillMaxSize().verticalScroll(rememberScrollState())) {
        Header("Devices", if (config.signedIn) config.relayUrl else "Every device you own, one account")
        Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(14.dp)) {
            Card(Modifier.fillMaxWidth()) {
                Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                    Text("Hands on this phone", fontWeight = FontWeight.SemiBold)
                    Text(if (handsOn) "On. Nemo can see the screen and tap, swipe and type when a task needs it; a STOP button floats on screen while it works."
                    else "Off. Turn on “Nemo hands” in Accessibility so Nemo can work apps that have no other way in.",
                        fontSize = 14.sp, color = MaterialTheme.colorScheme.onSurfaceVariant)
                    if (!handsOn) Button(onClick = { context.startActivity(Intent(android.provider.Settings.ACTION_ACCESSIBILITY_SETTINGS).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)) }) {
                        Text("Open Accessibility settings")
                    }
                }
            }

            if (!config.signedIn) Card(Modifier.fillMaxWidth()) {
                Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(10.dp)) {
                    Text("Sign in to a relay", fontWeight = FontWeight.SemiBold)
                    Text("Your computer and this phone meet on the relay: hand each other tasks, and approvals come to the device in your hand.",
                        fontSize = 14.sp, color = MaterialTheme.colorScheme.onSurfaceVariant)
                    OutlinedTextField(relay, { relay = it }, Modifier.fillMaxWidth(), label = { Text("Relay URL") }, singleLine = true,
                        keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Uri))
                    OutlinedTextField(identifier, { identifier = it }, Modifier.fillMaxWidth(), label = { Text("E-mail or +86 mobile") }, singleLine = true)
                    if (sent) OutlinedTextField(code, { code = it }, Modifier.fillMaxWidth(), label = { Text("Code") }, singleLine = true,
                        keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Number))
                    Button(enabled = relay.isNotBlank() && identifier.isNotBlank() && (!sent || code.isNotBlank()), onClick = {
                        scope.launch {
                            status = runCatching {
                                withContext(Dispatchers.IO) {
                                    if (!sent) { val hint = Account.sendCode(relay, identifier); sent = true; "Code sent to $hint." }
                                    else {
                                        val token = Account.verify(relay, identifier, code, config.deviceId, config.deviceName)
                                        graph.settings.update { copy(relayUrl = relay.trimEnd('/')) }
                                        graph.settings.hubToken = token
                                        "Signed in."
                                    }
                                }
                            }.getOrElse { it.message ?: "Failed." }
                        }
                    }) { Text(if (sent) "Sign in" else "Send code") }
                    if (status.isNotEmpty()) Text(status, fontSize = 13.sp)
                }
            } else {
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Icon(Icons.Default.Sync, null, tint = if (online) MaterialTheme.colorScheme.primary else Warn)
                    Text(if (online) "  Connected" else "  Connecting… $error", Modifier.weight(1f), fontSize = 14.sp)
                    TextButton(onClick = { graph.settings.hubToken = null }) { Text("Sign out") }
                }
                devices.forEach { d ->
                    val me = d.id == config.deviceId
                    Card(Modifier.fillMaxWidth()) {
                        Column(Modifier.padding(14.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                            Row(verticalAlignment = Alignment.CenterVertically) {
                                Icon(if (d.kind == "phone") Icons.Default.Smartphone else Icons.Default.Laptop, null, Modifier.size(28.dp),
                                    tint = if (d.online) MaterialTheme.colorScheme.primary else MaterialTheme.colorScheme.onSurfaceVariant)
                                Column(Modifier.weight(1f).padding(start = 12.dp)) {
                                    Text(d.name + if (me) "  (this phone)" else "", fontWeight = FontWeight.SemiBold)
                                    Text("${d.os} · ${if (d.online) "online" else "offline"}", fontSize = 12.sp, color = MaterialTheme.colorScheme.onSurfaceVariant)
                                }
                            }
                            if (d.online && !me) {
                                Text("Start a message with @${d.name} to have it do the task.", fontSize = 12.sp, color = MaterialTheme.colorScheme.onSurfaceVariant)
                                Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                                    OutlinedButton(onClick = { scope.launch { info = graph.hub.call(d.id, "info", JsonObject(emptyMap())).toString() } }) { Text("Info") }
                                    OutlinedButton(onClick = {
                                        scope.launch { graph.hub.call(d.id, "notify", buildJsonObject { put("title", "Nemo"); put("body", "Hello from ${config.deviceName} 👋") }) }
                                    }) { Text("Ping") }
                                }
                            }
                        }
                    }
                }
                if (info.isNotEmpty()) Text(info, fontSize = 12.sp, fontFamily = FontFamily.Monospace)
            }
        }
    }
}

@Composable
fun MemoryScreen(graph: AppGraph) {
    val facts by graph.memory.facts.collectAsState()
    var draft by remember { mutableStateOf("") }
    Column(Modifier.fillMaxSize()) {
        Header("Memory", "What Nemo knows about you")
        Row(Modifier.padding(horizontal = 16.dp), verticalAlignment = Alignment.CenterVertically) {
            OutlinedTextField(draft, { draft = it }, Modifier.weight(1f), placeholder = { Text("Add a fact, e.g. I'm vegetarian") }, singleLine = true)
            IconButton(onClick = { if (draft.isNotBlank()) { graph.memory.add(draft); draft = "" } }) { Icon(Icons.Default.Add, "Add") }
        }
        LazyColumn(contentPadding = PaddingValues(16.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
            if (facts.isEmpty()) item { Text("Nothing yet. Nemo saves lasting facts as you talk.", color = MaterialTheme.colorScheme.onSurfaceVariant) }
            items(facts, key = { it.id }) { f ->
                Row(Modifier.fillMaxWidth().background(MaterialTheme.colorScheme.surfaceContainer, RoundedCornerShape(12.dp)).padding(start = 14.dp),
                    verticalAlignment = Alignment.CenterVertically) {
                    Column(Modifier.weight(1f).padding(vertical = 10.dp)) {
                        Text(f.content)
                        Text(f.category, fontSize = 12.sp, color = MaterialTheme.colorScheme.onSurfaceVariant)
                    }
                    IconButton(onClick = { graph.memory.remove(f.id) }) { Icon(Icons.Default.Delete, "Forget", tint = MaterialTheme.colorScheme.onSurfaceVariant) }
                }
            }
        }
    }
}

private fun isLocal(url: String): Boolean {
    val host = Uri.parse(url).host.orEmpty()
    return host == "localhost" || host.startsWith("10.") || host.startsWith("192.168.") || host.startsWith("127.") ||
        Regex("^172\\.(1[6-9]|2\\d|3[01])\\.").containsMatchIn(host) || host.endsWith(".local") || host.endsWith(".lan")
}

@Composable
fun SettingsScreen(graph: AppGraph) {
    val config by graph.settings.config.collectAsState()
    var name by remember(config) { mutableStateOf(config.name) }
    var provider by remember(config) { mutableStateOf(config.provider) }
    var baseUrl by remember(config) { mutableStateOf(config.baseUrl) }
    var model by remember(config) { mutableStateOf(config.model) }
    var key by remember { mutableStateOf("") }
    var mode by remember(config) { mutableStateOf(config.mode) }
    var saved by remember { mutableStateOf(false) }
    val kind = PROVIDERS.firstOrNull { it.id == provider }?.kind ?: "openai"

    Column(Modifier.fillMaxSize().verticalScroll(rememberScrollState())) {
        Header("Settings", "Nemo for Android 0.2.0")
        Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(12.dp)) {
            Text("Model", fontWeight = FontWeight.SemiBold)
            PROVIDERS.forEach { p ->
                val disabled = p.id == "relay" && !config.signedIn
                Row(Modifier.fillMaxWidth().selectable(selected = provider == p.id, enabled = !disabled) {
                    provider = p.id
                    if (p.baseUrl.isNotEmpty()) baseUrl = p.baseUrl
                    if (p.model.isNotEmpty()) model = p.model
                }, verticalAlignment = Alignment.CenterVertically) {
                    RadioButton(selected = provider == p.id, onClick = null, enabled = !disabled)
                    Text(if (p.id == "relay") "relay (free allowance${if (disabled) " — sign in on Devices" else ""})" else p.id,
                        Modifier.padding(start = 8.dp), color = if (disabled) MaterialTheme.colorScheme.onSurfaceVariant else MaterialTheme.colorScheme.onSurface)
                }
            }
            if (provider != "relay") {
                OutlinedTextField(baseUrl, { baseUrl = it }, Modifier.fillMaxWidth(), label = { Text("Base URL") }, singleLine = true)
                if (baseUrl.startsWith("http://") && !isLocal(baseUrl))
                    Text("This URL is not encrypted; your key and chats would cross the internet in the clear.", color = Danger, fontSize = 12.sp)
                OutlinedTextField(model, { model = it }, Modifier.fillMaxWidth(), label = { Text("Model") }, singleLine = true)
                OutlinedTextField(key, { key = it }, Modifier.fillMaxWidth(), label = { Text("API key") }, singleLine = true,
                    visualTransformation = PasswordVisualTransformation(),
                    placeholder = { Text(if (config.hasKey) "saved — leave empty to keep" else "paste your key") })
                Text("Stored encrypted with a key held by Android's Keystore.", fontSize = 12.sp, color = MaterialTheme.colorScheme.onSurfaceVariant)
            }
            OutlinedTextField(name, { name = it }, Modifier.fillMaxWidth(), label = { Text("Agent name") }, singleLine = true)

            HorizontalDivider()
            Text("Asking first", fontWeight = FontWeight.SemiBold)
            listOf(
                Mode.ASK to "Ask: anything sensitive (paying, sending, deleting, calls) waits for you.",
                Mode.STRICT to "Strict: only looking and reading happen without asking.",
                Mode.AUTO to "Auto: nothing asks, except payment-like taps and sending private data out, which always ask.",
            ).forEach { (m, label) ->
                Row(Modifier.fillMaxWidth().selectable(selected = mode == m) { mode = m }, verticalAlignment = Alignment.CenterVertically) {
                    RadioButton(selected = mode == m, onClick = null)
                    Text(label, Modifier.padding(start = 8.dp), fontSize = 14.sp)
                }
            }
            if (graph.settings.alwaysGrants.isNotEmpty()) {
                Text("Always allowed: ${graph.settings.alwaysGrants.joinToString()}", fontSize = 12.sp, color = MaterialTheme.colorScheme.onSurfaceVariant)
                OutlinedButton(onClick = { graph.settings.alwaysGrants = emptySet() }) { Text("Ask again for everything") }
            }
            Button(onClick = {
                graph.settings.update { copy(name = name.ifBlank { "Nemo" }, provider = provider, kind = kind, baseUrl = baseUrl.trim(), model = model.trim(), mode = mode) }
                if (key.isNotBlank()) { graph.settings.apiKey = key.trim(); key = "" }
                saved = true
            }, Modifier.fillMaxWidth()) { Text("Save") }
            if (saved) Text("Saved.", color = MaterialTheme.colorScheme.primary)
        }
    }
}
