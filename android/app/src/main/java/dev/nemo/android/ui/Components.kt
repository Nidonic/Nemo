package dev.nemo.android.ui

import android.graphics.BitmapFactory
import android.util.Base64
import androidx.compose.foundation.Image
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.isSystemInDarkTheme
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Block
import androidx.compose.material.icons.filled.Check
import androidx.compose.material.icons.filled.Close
import androidx.compose.material.icons.filled.Shield
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Text
import androidx.compose.material3.darkColorScheme
import androidx.compose.material3.lightColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.asImageBitmap
import androidx.compose.ui.text.AnnotatedString
import androidx.compose.ui.text.SpanStyle
import androidx.compose.ui.text.buildAnnotatedString
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.text.withStyle
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import dev.nemo.android.agent.ApprovalRequest

val Teal = Color(0xFF0F8F86)
val TealLight = Color(0xFF2DD4BF)
val Warn = Color(0xFFB45309)
val Danger = Color(0xFFDC2626)

@Composable
fun NemoTheme(content: @Composable () -> Unit) {
    val dark = isSystemInDarkTheme()
    val colors = if (dark) darkColorScheme(
        primary = TealLight, onPrimary = Color(0xFF052421), primaryContainer = Color(0xFF12322F), onPrimaryContainer = Color(0xFFCCFBF1),
        background = Color(0xFF0F1514), surface = Color(0xFF0F1514), surfaceVariant = Color(0xFF1A2322), surfaceContainer = Color(0xFF161E1D),
    ) else lightColorScheme(
        primary = Teal, onPrimary = Color.White, primaryContainer = Color(0xFFDCF3EF), onPrimaryContainer = Color(0xFF053B37),
        background = Color(0xFFF6F8F7), surface = Color(0xFFF6F8F7), surfaceVariant = Color(0xFFEAF0EE), surfaceContainer = Color.White,
    )
    MaterialTheme(colorScheme = colors, content = content)
}

/** Enough Markdown for chat: headings, bullets, code blocks, **bold**, `code`. */
fun markdown(text: String, codeBg: Color): AnnotatedString = buildAnnotatedString {
    var inFence = false
    val lines = text.lines()
    lines.forEachIndexed { index, raw ->
        if (raw.trimStart().startsWith("```")) { inFence = !inFence; return@forEachIndexed }
        if (inFence) {
            withStyle(SpanStyle(fontFamily = FontFamily.Monospace, background = codeBg, fontSize = 13.sp)) { append(raw) }
        } else {
            val heading = Regex("^(#{1,6})\\s+(.*)").find(raw)
            val bullet = Regex("^\\s*[-*+]\\s+(.*)").find(raw)
            when {
                heading != null -> withStyle(SpanStyle(fontWeight = FontWeight.Bold, fontSize = 17.sp)) { inline(heading.groupValues[2], codeBg) }
                bullet != null -> { append("•  "); inline(bullet.groupValues[1], codeBg) }
                else -> inline(raw, codeBg)
            }
        }
        if (index < lines.lastIndex) append('\n')
    }
}

private fun AnnotatedString.Builder.inline(line: String, codeBg: Color) {
    val token = Regex("\\*\\*([^*]+)\\*\\*|`([^`]+)`")
    var at = 0
    for (m in token.findAll(line)) {
        append(line.substring(at, m.range.first))
        if (m.groupValues[1].isNotEmpty()) withStyle(SpanStyle(fontWeight = FontWeight.SemiBold)) { append(m.groupValues[1]) }
        else withStyle(SpanStyle(fontFamily = FontFamily.Monospace, background = codeBg)) { append(m.groupValues[2]) }
        at = m.range.last + 1
    }
    append(line.substring(at))
}

@Composable
fun Markdown(text: String, modifier: Modifier = Modifier) {
    Text(markdown(text, MaterialTheme.colorScheme.surfaceVariant), modifier = modifier, style = MaterialTheme.typography.bodyLarge)
}

@Composable
fun Base64Image(data: String, modifier: Modifier = Modifier) {
    val bitmap = remember(data) {
        runCatching { Base64.decode(data, Base64.DEFAULT).let { BitmapFactory.decodeByteArray(it, 0, it.size) } }.getOrNull()
    } ?: return
    Image(bitmap.asImageBitmap(), contentDescription = "screenshot", modifier = modifier)
}

@Composable
fun ToolRow(name: String, summary: String, status: String, output: String, image: String? = null) {
    var open by remember { mutableStateOf(false) }
    val shape = RoundedCornerShape(12.dp)
    Column(Modifier.fillMaxWidth().border(1.dp, MaterialTheme.colorScheme.outlineVariant, shape).background(MaterialTheme.colorScheme.surfaceContainer, shape)) {
        Row(Modifier.fillMaxWidth().clickable { open = !open }.padding(horizontal = 12.dp, vertical = 9.dp), verticalAlignment = Alignment.CenterVertically) {
            Text(name.replace('_', ' '), fontWeight = FontWeight.SemiBold, fontSize = 14.sp)
            Text(summary, Modifier.weight(1f).padding(horizontal = 8.dp), color = MaterialTheme.colorScheme.onSurfaceVariant, fontSize = 13.sp,
                fontFamily = FontFamily.Monospace, maxLines = 1, overflow = TextOverflow.Ellipsis)
            when (status) {
                "run" -> CircularProgressIndicator(Modifier.size(16.dp), strokeWidth = 2.dp)
                "ok" -> Icon(Icons.Default.Check, null, tint = Color(0xFF16A34A), modifier = Modifier.size(18.dp))
                "refused" -> Icon(Icons.Default.Block, null, tint = Warn, modifier = Modifier.size(18.dp))
                else -> Icon(Icons.Default.Close, null, tint = Danger, modifier = Modifier.size(18.dp))
            }
        }
        if (image != null) Base64Image(image, Modifier.padding(start = 12.dp, end = 12.dp, bottom = 10.dp).heightIn(max = 260.dp))
        if (open && output.isNotEmpty()) Text(output, Modifier.fillMaxWidth().background(MaterialTheme.colorScheme.surfaceVariant).padding(12.dp),
            fontFamily = FontFamily.Monospace, fontSize = 12.sp)
    }
}

private val SCOPE_LABEL = mapOf("once" to "Allow once", "conversation" to "In this chat", "session" to "Until restart", "always" to "Always")

@Composable
fun ApprovalCard(req: ApprovalRequest, onDecide: (Boolean, String) -> Unit) {
    val shape = RoundedCornerShape(14.dp)
    Column(
        Modifier.fillMaxWidth().border(1.dp, Warn.copy(alpha = 0.6f), shape).background(Warn.copy(alpha = 0.08f), shape).padding(14.dp),
        verticalArrangement = Arrangement.spacedBy(8.dp),
    ) {
        Row(verticalAlignment = Alignment.Top) {
            Icon(Icons.Default.Shield, null, tint = Warn, modifier = Modifier.size(20.dp))
            Text(req.reason, Modifier.padding(start = 8.dp), color = Warn, fontWeight = FontWeight.SemiBold)
        }
        Text(buildAnnotatedString {
            withStyle(SpanStyle(fontWeight = FontWeight.Bold)) { append(req.tool.replace('_', ' ')) }
            append("  ·  ${req.risk}")
            if (req.from != "this phone") append("  ·  on ${req.from}")
        }, fontSize = 14.sp)
        if (req.preview.isNotBlank()) Text(req.preview, Modifier.fillMaxWidth().background(MaterialTheme.colorScheme.surfaceContainer, RoundedCornerShape(8.dp)).padding(10.dp),
            fontFamily = FontFamily.Monospace, fontSize = 13.sp)
        req.warnings.forEach { Text("• $it", color = Danger, fontSize = 13.sp) }
        Row(horizontalArrangement = Arrangement.spacedBy(6.dp)) {
            Button(onClick = { onDecide(true, "once") }) { Text("Allow once") }
            OutlinedButton(onClick = { onDecide(false, "once") }, colors = ButtonDefaults.outlinedButtonColors(contentColor = Danger)) { Text("Don't allow") }
        }
        val more = req.scopes.filter { it != "once" }
        if (more.isNotEmpty()) Row(Modifier.horizontalScroll(rememberScrollState()), horizontalArrangement = Arrangement.spacedBy(6.dp)) {
            more.forEach { s -> OutlinedButton(onClick = { onDecide(true, s) }) { Text(SCOPE_LABEL[s] ?: s, fontSize = 12.sp) } }
        }
    }
}
