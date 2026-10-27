# Nemo for Android

A native Kotlin + Jetpack Compose app. The phone runs its own Nemo agent and is a full device on your account: it does things on the phone, and it is the device in your hand that approves what your computer does.

## What it does

- **Its own agent.** The same loop as the desktop runtime: streaming answers with a collapsible reasoning block, a mid-run inbox, context pruning at user turns, only the newest three screenshots sent as images, stuck detection, and a retry when the thinking budget runs out. It talks to any OpenAI-compatible endpoint or Anthropic, or to the relay's free allowance once signed in.
- **Tools.** `open_app` (apps by name, URLs and deep links), `list_apps`, `web_fetch`, `phone_screen` and `phone_act` (hands), `remember` / `recall`, `notify`, `device_info`, `device_list`, `device_task` and `terminate`.
- **Hands.** An AccessibilityService that Nemo uses only during a task:
  - It takes screenshots with the floating capsule hidden, so the capsule is never in them.
  - It acts on the shared 0..999 grid: `tap`, `double_tap`, `long_press`, `swipe`, `type` (into the focused field), and `key` (Back, Home, Recents, Enter, Notifications).
  - `takeover` hands the phone to you for passwords and CAPTCHAs, and `finish` ends the screen task.
  - While it works, a STOP capsule floats over every app.
- **Sentinel.**
  - Calls are SAFE, MODERATE or SENSITIVE, and the mode is ask, strict or auto.
  - Payment, send and delete labels always ask. These are 确认支付, 转账, 提交订单, 发送, 删除, pay, checkout, send and similar.
  - Once a conversation has read private data, sending data to an unknown host always asks.
  - Approvals can last once, for the chat, for the session or always, and are bound to a `tool:target` key.
- **Your other devices.**
  - Sign in to a relay with an e-mail or +86 one-time code, and the phone joins the hub as a `phone`.
  - `@MyPC do X` hands the whole task to the computer. The computer's approval questions appear on the phone, and your answer goes back.
  - The computer can ask the phone for `info`, `notify`, `screen`, `open` and `task`, all through the phone's own Sentinel.
  - A foreground service keeps the phone reachable in the background, and approvals arriving then become notifications.
- **Private by default.** API keys and the relay token are encrypted with a key held in the Android Keystore. Backups are disabled, and chats and memory stay on the phone.

## Build

You need the Android SDK (platform 36, build-tools 36.0.0) and JDK 17+.

```bash
./gradlew :app:testDebugUnitTest :app:assembleDebug
# apk: app/build/outputs/apk/debug/app-debug.apk
```

On a disk that is short of space, move all build output elsewhere:

```bash
GRADLE_USER_HOME=D:/gradle-home ./gradlew -PnemoBuildRoot=D:/nemo-build/out :app:assembleDebug
```

Install with `adb install app-debug.apk`. Then open **Devices** and turn on *Nemo hands*, and open **Settings** to pick a model, or sign in to a relay.

## Not here (yet)

- There is no on-device Linux (no shell or Python on the phone). For anything that needs a computer, use `device_task` and your PC does it.
- There is no iOS app.
