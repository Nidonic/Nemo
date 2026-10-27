package dev.nemo.android.ui

import androidx.activity.compose.BackHandler
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.imePadding
import androidx.compose.foundation.layout.navigationBarsPadding
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material.icons.automirrored.filled.Send
import androidx.compose.material.icons.filled.Psychology
import androidx.compose.material.icons.filled.Stop
import androidx.compose.material.icons.filled.TouchApp
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.FilledIconButton
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.IconButtonDefaults
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.material3.TopAppBar
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import dev.nemo.android.AppGraph
import dev.nemo.android.agent.LiveTurn
import dev.nemo.android.data.Msg
import kotlinx.coroutines.launch
import kotlinx.serialization.json.JsonPrimitive

@Composable
private fun Thinking(text: String, live: Boolean) {
    var open by remember { mutableStateOf(false) }
    Column(Modifier.fillMaxWidth().background(MaterialTheme.colorScheme.surfaceVariant, RoundedCornerShape(12.dp)).clickable { open = !open }.padding(10.dp)) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Icon(Icons.Default.Psychology, null, Modifier.size(16.dp), tint = MaterialTheme.colorScheme.onSurfaceVariant)
            Text(if (live) "  Thinking…" else "  Thought it through", color = MaterialTheme.colorScheme.onSurfaceVariant, fontSize = 13.sp)
        }
        if (open) Text(text, Modifier.padding(top = 6.dp), color = MaterialTheme.colorScheme.onSurfaceVariant, fontSize = 13.sp)
    }
}

@Composable
private fun UserBubble(text: String, pending: Boolean = false) {
    Box(Modifier.fillMaxWidth(), contentAlignment = Alignment.CenterEnd) {
        Text(text, Modifier.widthIn(max = 300.dp)
            .background(MaterialTheme.colorScheme.primary.copy(alpha = if (pending) 0.7f else 1f), RoundedCornerShape(18.dp, 18.dp, 4.dp, 18.dp))
            .padding(horizontal = 14.dp, vertical = 9.dp), color = MaterialTheme.colorScheme.onPrimary)
    }
}

private sealed interface ChatRow {
    data class User(val m: Msg) : ChatRow
    data class Event(val m: Msg) : ChatRow
    data class Agent(val m: Msg, val results: Map<String, Msg>) : ChatRow
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun ChatScreen(graph: AppGraph, conversationId: String, onBack: () -> Unit) {
    BackHandler(onBack = onBack)
    val flow = remember(conversationId) { graph.store.flow(conversationId) } ?: return
    val conv by flow.collectAsState()
    val live by graph.agents.live.collectAsState()
    val running by graph.agents.running.collectAsState()
    val approvals by graph.approvals.pending.collectAsState()
    val takeovers by graph.agents.takeovers.collectAsState()
    val hands by graph.agents.hands.collectAsState()
    val scope = rememberCoroutineScope()
    var draft by remember { mutableStateOf("") }
    val list = rememberLazyListState()
    val isRunning = conversationId in running
    val turn: LiveTurn? = live[conversationId]
    val mine = approvals.filter { it.conversationId == conversationId }

    val results = conv.messages.filter { it.role == "tool" }.associateBy { it.toolCallId.orEmpty() }
    val rows = conv.messages.mapNotNull { m ->
        when {
            m.role == "user" -> ChatRow.User(m)
            m.event -> ChatRow.Event(m)
            m.role == "assistant" -> ChatRow.Agent(m, results)
            else -> null
        }
    }
    val total = rows.size + (if (turn != null) 1 else 0) + mine.size + (if (conversationId in takeovers) 1 else 0)
    LaunchedEffect(total, turn?.text?.length, turn?.tools?.size) { if (total > 0) list.animateScrollToItem(total - 1) }

    Column(Modifier.fillMaxSize().navigationBarsPadding().imePadding()) {
        TopAppBar(
            title = { Text(conv.title, maxLines = 1, overflow = TextOverflow.Ellipsis) },
            navigationIcon = { IconButton(onClick = onBack) { Icon(Icons.AutoMirrored.Filled.ArrowBack, "Back") } },
        )
        hands[conversationId]?.let { label ->
            Row(Modifier.fillMaxWidth().background(MaterialTheme.colorScheme.primaryContainer).padding(horizontal = 16.dp, vertical = 8.dp),
                verticalAlignment = Alignment.CenterVertically) {
                Icon(Icons.Default.TouchApp, null, Modifier.size(18.dp))
                Text("  $label", Modifier.weight(1f), maxLines = 1, overflow = TextOverflow.Ellipsis, fontSize = 14.sp)
                Button(onClick = { graph.agents.stop(conversationId) }, colors = ButtonDefaults.buttonColors(containerColor = Danger)) { Text("STOP") }
            }
        }
        LazyColumn(Modifier.weight(1f).fillMaxWidth(), state = list, verticalArrangement = Arrangement.spacedBy(12.dp),
            contentPadding = PaddingValues(16.dp)) {
            if (rows.isEmpty() && turn == null) item {
                Text("Ask me to do something on this phone, or start with @YourPC to send a task to your computer.",
                    color = MaterialTheme.colorScheme.onSurfaceVariant, modifier = Modifier.padding(top = 40.dp))
            }
            items(rows, key = { r -> when (r) { is ChatRow.User -> r.m.id; is ChatRow.Event -> r.m.id; is ChatRow.Agent -> r.m.id } }) { r ->
                when (r) {
                    is ChatRow.User -> UserBubble(r.m.content)
                    is ChatRow.Event -> Text(r.m.content, Modifier.fillMaxWidth(), fontSize = 13.sp,
                        color = if (r.m.ok == false) Danger else MaterialTheme.colorScheme.onSurfaceVariant)
                    is ChatRow.Agent -> Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                        if (r.m.reasoning.isNotBlank()) Thinking(r.m.reasoning, false)
                        if (r.m.content.isNotBlank()) Markdown(r.m.content)
                        r.m.toolCalls.forEach { c ->
                            val res = r.results[c.id]
                            val status = when { res == null -> if (isRunning) "run" else "err"; res.refused -> "refused"; res.ok == false -> "err"; else -> "ok" }
                            val summary = listOf("label", "text", "name", "url", "query", "action").firstNotNullOfOrNull { k ->
                                (c.arguments[k] as? JsonPrimitive)?.content
                            }.orEmpty()
                            ToolRow(c.name, summary, status, res?.content.orEmpty(), res?.image)
                        }
                    }
                }
            }
            if (turn != null) item(key = "live") {
                Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                    turn.remote.forEach { Text(it, fontSize = 13.sp, color = MaterialTheme.colorScheme.onSurfaceVariant) }
                    if (turn.reasoning.isNotBlank()) Thinking(turn.reasoning, true)
                    if (turn.text.isNotBlank()) Markdown(turn.text + " ▍")
                    if (turn.text.isBlank() && turn.reasoning.isBlank()) Text("Working…", color = MaterialTheme.colorScheme.onSurfaceVariant, fontSize = 14.sp)
                }
            }
            items(mine, key = { it.id }) { req ->
                ApprovalCard(req) { allow, s -> scope.launch { graph.approvals.resolve(req.id, allow, s) } }
            }
            takeovers[conversationId]?.let { reason ->
                item(key = "takeover") {
                    Column(Modifier.fillMaxWidth().background(MaterialTheme.colorScheme.primaryContainer, RoundedCornerShape(14.dp)).padding(14.dp),
                        verticalArrangement = Arrangement.spacedBy(8.dp)) {
                        Text("Your turn", fontWeight = FontWeight.Bold)
                        Text(reason)
                        Button(onClick = { graph.agents.resolveTakeover(conversationId) }) { Text("Done, carry on") }
                    }
                }
            }
        }
        Row(Modifier.fillMaxWidth().padding(12.dp), verticalAlignment = Alignment.Bottom) {
            OutlinedTextField(draft, { draft = it }, Modifier.weight(1f), placeholder = { Text(if (isRunning) "Add to the task…" else "Ask Nemo…") },
                maxLines = 5, shape = RoundedCornerShape(20.dp))
            Spacer(Modifier.size(8.dp))
            if (isRunning) {
                FilledIconButton(onClick = { graph.agents.stop(conversationId) }, colors = IconButtonDefaults.filledIconButtonColors(containerColor = Danger)) {
                    Icon(Icons.Default.Stop, "Stop")
                }
                Spacer(Modifier.size(4.dp))
            }
            FilledIconButton(onClick = {
                val text = draft.trim()
                if (text.isNotEmpty()) { graph.agents.send(conversationId, text); draft = "" }
            }, enabled = draft.isNotBlank()) { Icon(Icons.AutoMirrored.Filled.Send, "Send") }
        }
    }
}
