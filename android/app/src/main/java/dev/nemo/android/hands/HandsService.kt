package dev.nemo.android.hands

import android.accessibilityservice.AccessibilityService
import android.accessibilityservice.GestureDescription
import android.graphics.Bitmap
import android.graphics.Color
import android.graphics.Path
import android.graphics.PixelFormat
import android.graphics.drawable.GradientDrawable
import android.os.Bundle
import android.util.Base64
import android.util.TypedValue
import android.view.Display
import android.view.Gravity
import android.view.View
import android.view.WindowManager
import android.view.accessibility.AccessibilityEvent
import android.view.accessibility.AccessibilityNodeInfo
import android.widget.Button
import android.widget.ImageView
import android.widget.LinearLayout
import android.widget.TextView
import dev.nemo.android.R
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.suspendCancellableCoroutine
import kotlinx.coroutines.withContext
import java.io.ByteArrayOutputStream
import kotlin.coroutines.resume
import kotlin.coroutines.resumeWithException

class HandsUnavailable(message: String) : Exception(message)

data class Shot(val jpegBase64: String, val width: Int, val height: Int)

/** Nemo's hands on this phone: see the screen, tap, swipe, type, and the floating STOP capsule. */
class HandsService : AccessibilityService() {

    companion object {
        @Volatile private var instance: HandsService? = null
        private val _enabled = MutableStateFlow(false)
        val enabled: StateFlow<Boolean> = _enabled

        fun get(): HandsService = instance ?: throw HandsUnavailable(
            "Nemo's hands are off. Turn them on in Settings → Accessibility → Nemo hands (the Devices tab has a shortcut).")
    }

    private var capsule: View? = null
    private var capsuleLabel: TextView? = null
    private var onStop: (() -> Unit)? = null

    override fun onServiceConnected() {
        instance = this
        _enabled.value = true
    }

    override fun onDestroy() {
        hideCapsule()
        instance = null
        _enabled.value = false
        super.onDestroy()
    }

    override fun onAccessibilityEvent(event: AccessibilityEvent?) = Unit
    override fun onInterrupt() = Unit

    fun size(): Pair<Int, Int> {
        val b = getSystemService(WindowManager::class.java).currentWindowMetrics.bounds
        return b.width() to b.height()
    }

    // ------------------------------------------------------------------ seeing

    suspend fun screenshot(): Shot {
        val view = capsule
        // keep the capsule out of the picture Nemo looks at
        if (view != null) withContext(Dispatchers.Main) { view.visibility = View.INVISIBLE }
        if (view != null) delay(80)
        try {
            val bitmap = suspendCancellableCoroutine<Bitmap> { cont ->
                takeScreenshot(Display.DEFAULT_DISPLAY, mainExecutor, object : TakeScreenshotCallback {
                    override fun onSuccess(result: ScreenshotResult) {
                        val hw = Bitmap.wrapHardwareBuffer(result.hardwareBuffer, result.colorSpace)
                        val soft = hw?.copy(Bitmap.Config.ARGB_8888, false)
                        result.hardwareBuffer.close()
                        if (soft != null) cont.resume(soft) else cont.resumeWithException(HandsUnavailable("The screenshot was empty."))
                    }

                    override fun onFailure(errorCode: Int) {
                        cont.resumeWithException(HandsUnavailable("Android refused the screenshot (code $errorCode); try again in a moment."))
                    }
                })
            }
            return withContext(Dispatchers.Default) { encode(bitmap) }
        } finally {
            if (view != null) withContext(Dispatchers.Main) { view.visibility = View.VISIBLE }
        }
    }

    private fun encode(bitmap: Bitmap): Shot {
        val (w, h) = bitmap.width to bitmap.height
        val scale = minOf(1f, 1280f / maxOf(w, h))
        val small = if (scale < 1f) Bitmap.createScaledBitmap(bitmap, (w * scale).toInt(), (h * scale).toInt(), true) else bitmap
        val out = ByteArrayOutputStream()
        small.compress(Bitmap.CompressFormat.JPEG, 70, out)
        return Shot(Base64.encodeToString(out.toByteArray(), Base64.NO_WRAP), w, h)
    }

    /** The visible text and labelled controls, for a quick read without a screenshot. */
    fun screenText(limit: Int = 4000): String {
        val root = rootInActiveWindow ?: return "(no window)"
        val sb = StringBuilder("App: ${root.packageName}\n")
        fun walk(n: AccessibilityNodeInfo?, depth: Int) {
            if (n == null || sb.length > limit || depth > 30) return
            val label = (n.text ?: n.contentDescription)?.toString()?.trim()
            if (!label.isNullOrEmpty()) sb.append(if (n.isClickable) "[button] " else "").append(label).append('\n')
            for (i in 0 until n.childCount) walk(n.getChild(i), depth + 1)
        }
        walk(root, 0)
        return sb.toString().take(limit)
    }

    // ------------------------------------------------------------------ acting

    private suspend fun gesture(path: Path, durationMs: Long): Boolean = suspendCancellableCoroutine { cont ->
        val g = GestureDescription.Builder().addStroke(GestureDescription.StrokeDescription(path, 0, durationMs.coerceIn(1, 10_000))).build()
        val sent = dispatchGesture(g, object : GestureResultCallback() {
            override fun onCompleted(d: GestureDescription?) { if (cont.isActive) cont.resume(true) }
            override fun onCancelled(d: GestureDescription?) { if (cont.isActive) cont.resume(false) }
        }, null)
        if (!sent && cont.isActive) cont.resume(false)
    }

    private fun point(x: Int, y: Int): Path {
        val (w, h) = size()
        val (sx, sy) = Grid.toScreen(x, y, w, h)
        return Path().apply { moveTo(sx.toFloat(), sy.toFloat()) }
    }

    suspend fun tap(x: Int, y: Int) = gesture(point(x, y), 60)

    suspend fun doubleTap(x: Int, y: Int): Boolean {
        val first = tap(x, y)
        delay(90)
        return first && tap(x, y)
    }

    suspend fun longPress(x: Int, y: Int, ms: Long = 800) = gesture(point(x, y), ms)

    suspend fun swipe(x: Int, y: Int, x2: Int, y2: Int, ms: Long = 400): Boolean {
        val (w, h) = size()
        val (sx, sy) = Grid.toScreen(x, y, w, h)
        val (ex, ey) = Grid.toScreen(x2, y2, w, h)
        return gesture(Path().apply { moveTo(sx.toFloat(), sy.toFloat()); lineTo(ex.toFloat(), ey.toFloat()) }, ms)
    }

    fun type(text: String, clearFirst: Boolean): Boolean {
        val node = rootInActiveWindow?.findFocus(AccessibilityNodeInfo.FOCUS_INPUT)
            ?: throw HandsUnavailable("No text field has focus. Tap the field first, then type.")
        val current = if (clearFirst) "" else node.text?.toString().orEmpty()
        val args = Bundle().apply { putCharSequence(AccessibilityNodeInfo.ACTION_ARGUMENT_SET_TEXT_CHARSEQUENCE, current + text) }
        return node.performAction(AccessibilityNodeInfo.ACTION_SET_TEXT, args)
    }

    fun key(name: String): Boolean = when (name) {
        "Back" -> performGlobalAction(GLOBAL_ACTION_BACK)
        "Home" -> performGlobalAction(GLOBAL_ACTION_HOME)
        "Recents" -> performGlobalAction(GLOBAL_ACTION_RECENTS)
        "Notifications" -> performGlobalAction(GLOBAL_ACTION_NOTIFICATIONS)
        "Enter" -> rootInActiveWindow?.findFocus(AccessibilityNodeInfo.FOCUS_INPUT)
            ?.performAction(AccessibilityNodeInfo.AccessibilityAction.ACTION_IME_ENTER.id) ?: false
        else -> throw HandsUnavailable("Unknown key $name.")
    }

    // ------------------------------------------------------------------ capsule

    private fun dp(v: Int) = TypedValue.applyDimension(TypedValue.COMPLEX_UNIT_DIP, v.toFloat(), resources.displayMetrics).toInt()

    fun showCapsule(label: String, stop: () -> Unit) {
        onStop = stop
        mainExecutor.execute {
            capsuleLabel?.let { it.text = label; return@execute }
            val bg = GradientDrawable().apply { cornerRadius = dp(28).toFloat(); setColor(Color.parseColor("#F2161E1D")) }
            val row = LinearLayout(this).apply {
                orientation = LinearLayout.HORIZONTAL
                gravity = Gravity.CENTER_VERTICAL
                background = bg
                setPadding(dp(8), dp(6), dp(6), dp(6))
                elevation = dp(6).toFloat()
            }
            row.addView(ImageView(this).apply { setImageResource(R.mipmap.ic_launcher) }, LinearLayout.LayoutParams(dp(32), dp(32)))
            val text = TextView(this).apply {
                this.text = label
                setTextColor(Color.WHITE)
                textSize = 14f
                maxLines = 1
                ellipsize = android.text.TextUtils.TruncateAt.END
                setPadding(dp(10), 0, dp(10), 0)
            }
            row.addView(text, LinearLayout.LayoutParams(dp(200), LinearLayout.LayoutParams.WRAP_CONTENT))
            row.addView(Button(this).apply {
                this.text = "■ STOP"
                setTextColor(Color.WHITE)
                background = GradientDrawable().apply { cornerRadius = dp(20).toFloat(); setColor(Color.parseColor("#DC2626")) }
                setOnClickListener { onStop?.invoke() }
            }, LinearLayout.LayoutParams(LinearLayout.LayoutParams.WRAP_CONTENT, dp(40)))
            val lp = WindowManager.LayoutParams(
                WindowManager.LayoutParams.WRAP_CONTENT, WindowManager.LayoutParams.WRAP_CONTENT,
                WindowManager.LayoutParams.TYPE_ACCESSIBILITY_OVERLAY,
                WindowManager.LayoutParams.FLAG_NOT_FOCUSABLE or WindowManager.LayoutParams.FLAG_LAYOUT_IN_SCREEN,
                PixelFormat.TRANSLUCENT,
            ).apply { gravity = Gravity.TOP or Gravity.CENTER_HORIZONTAL; y = dp(36) }
            getSystemService(WindowManager::class.java).addView(row, lp)
            capsule = row
            capsuleLabel = text
        }
    }

    fun hideCapsule() {
        mainExecutor.execute {
            capsule?.let { runCatching { getSystemService(WindowManager::class.java).removeView(it) } }
            capsule = null
            capsuleLabel = null
            onStop = null
        }
    }
}
