package dev.nemo.android

import android.Manifest
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.lifecycle.lifecycleScope
import dev.nemo.android.hub.HubService
import dev.nemo.android.ui.NemoRoot
import kotlinx.coroutines.flow.collectLatest
import kotlinx.coroutines.launch

class MainActivity : ComponentActivity() {
    private val graph get() = (application as NemoApp).graph

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        enableEdgeToEdge()
        requestPermissions(arrayOf(Manifest.permission.POST_NOTIFICATIONS), 1)
        setContent { NemoRoot(graph) }
        // stay on the hub while signed in
        lifecycleScope.launch {
            graph.settings.config.collectLatest { c -> if (c.signedIn) HubService.start(this@MainActivity) else HubService.stop(this@MainActivity) }
        }
    }

    override fun onResume() {
        super.onResume()
        graph.foreground.value = true
    }

    override fun onPause() {
        graph.foreground.value = false
        super.onPause()
    }
}
