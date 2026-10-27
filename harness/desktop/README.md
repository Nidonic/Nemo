# Nemo Desktop

An Electron shell around the Nemo runtime:

- **The daemon, managed:** on launch it reuses a Nemo already serving on `127.0.0.1:7860` (or `NEMO_DESKTOP_URL`); otherwise it finds a Python with Nemo installed and starts `nemo serve` on a free local port, then stops it on quit. Logs go to the app's logs folder (tray → *Open logs folder*).
- **The console in a native window.** Closing the window keeps Nemo in the tray, so routines, goals and devices keep working.
- **The hands capsule:** a small always-on-top bar shown while Nemo drives this computer's or a phone's screen, with what it is doing and a **STOP** button. It never takes focus and is excluded from screen capture, so it does not get in the way of the app being driven or show up in Nemo's own screenshots.
- **Native notifications** when Nemo needs your OK, wants you to take over (log-ins, CAPTCHAs), or another device pings this one. Clicking one opens the right conversation.
- **Tray menu:** open, new chat, stop the hands, start with the computer, logs, quit.
- **Shortcuts:** `Ctrl/Cmd+Shift+Space` shows or hides Nemo; `Ctrl/Cmd+Shift+.` stops every screen action at once.

## Run from source

```bash
pip install -e "../..[all]"     # the runtime, once (or use the repo's .venv, which is found automatically)
npm install
npm start
```

Python discovery order: `NEMO_PYTHON` → a bundled runtime in `resources/python` → the repository's `.venv` → `py` / `python` / `python3` on `PATH`.

`npm run smoke` (or `electron . --smoke=<dir>`) starts everything, saves screenshots of the window and the capsule to `<dir>`, prints `SMOKE OK …` and exits. It is handy in CI.

## Installers

```bash
npm run dist        # NSIS on Windows, DMG on macOS, AppImage + deb on Linux, into dist/
```

The installers contain the shell only; the machine still needs Python with `nemo-agent` installed. To ship a self-contained build, put a Python runtime with Nemo installed under `resources/python` (electron-builder `extraResources`); the app looks there first.
