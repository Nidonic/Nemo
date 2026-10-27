// Nemo Desktop: the agent daemon in a native window, a tray icon, the hands capsule, and native notifications.

const { app, BrowserWindow, Menu, Notification, Tray, globalShortcut, ipcMain, nativeImage, screen, shell } = require("electron");
const fs = require("node:fs");
const path = require("node:path");
const { Daemon } = require("./daemon");
const { follow, post } = require("./events");

const ASSETS = path.join(__dirname, "..", "assets");
const SMOKE = process.argv.find((a) => a.startsWith("--smoke"));
const SMOKE_DIR = SMOKE && SMOKE.includes("=") ? SMOKE.split("=")[1] : path.join(app.getPath("temp"), "nemo-smoke");

let mainWindow = null;
let capsule = null;
let tray = null;
let daemon = null;
let baseUrl = null;
let stopEvents = null;
let quitting = false;
let toldAboutTray = false;
const hands = new Map(); // thread id → { device, label }

if (!SMOKE && !app.requestSingleInstanceLock()) app.quit();

function icon(name) {
  return nativeImage.createFromPath(path.join(ASSETS, name));
}

// ---------------------------------------------------------------- windows

function createMainWindow() {
  mainWindow = new BrowserWindow({
    width: 1200,
    height: 820,
    minWidth: 380,
    minHeight: 560,
    title: "Nemo",
    icon: icon(process.platform === "win32" ? "icon.ico" : "icon.png"),
    show: false,
    autoHideMenuBar: true,
    backgroundColor: "#0f1514",
    webPreferences: { preload: path.join(__dirname, "setup-preload.js"), contextIsolation: true, sandbox: true, nodeIntegration: false },
  });
  mainWindow.once("ready-to-show", () => !SMOKE && mainWindow.show());
  mainWindow.on("close", (e) => {
    if (quitting || SMOKE) return;
    e.preventDefault();
    mainWindow.hide();
    if (!toldAboutTray && Notification.isSupported()) {
      toldAboutTray = true;
      new Notification({ title: "Nemo is still here", body: "Routines and goals keep running. Open Nemo from the tray.", icon: icon("icon.png") }).show();
    }
  });
  // links leave the app; navigation stays on the daemon
  mainWindow.webContents.setWindowOpenHandler(({ url }) => {
    if (/^https?:/.test(url)) shell.openExternal(url);
    return { action: "deny" };
  });
  mainWindow.webContents.on("will-navigate", (e, url) => {
    if (!baseUrl || !url.startsWith(baseUrl)) {
      e.preventDefault();
      if (/^https?:/.test(url)) shell.openExternal(url);
    }
  });
  mainWindow.loadFile(path.join(__dirname, "setup.html"));
}

function setupState(state) {
  if (mainWindow && mainWindow.webContents.getURL().startsWith("file:")) mainWindow.webContents.send("setup:state", state);
}

function showMain(hash) {
  if (!mainWindow) return;
  if (hash && baseUrl) mainWindow.webContents.executeJavaScript(`location.hash = ${JSON.stringify(hash)}`).catch(() => {});
  if (mainWindow.isMinimized()) mainWindow.restore();
  mainWindow.show();
  mainWindow.focus();
}

function createCapsule() {
  capsule = new BrowserWindow({
    width: 460,
    height: 64,
    frame: false,
    transparent: true,
    resizable: false,
    movable: true,
    alwaysOnTop: true,
    skipTaskbar: true,
    focusable: false, // never take focus from the app Nemo is driving
    show: false,
    hasShadow: false,
    webPreferences: { preload: path.join(__dirname, "capsule-preload.js"), contextIsolation: true, sandbox: true },
  });
  capsule.setAlwaysOnTop(true, "screen-saver");
  capsule.setVisibleOnAllWorkspaces(true, { visibleOnFullScreen: true });
  // keep the capsule out of screenshots, including the ones Nemo takes to see the screen
  capsule.setContentProtection(true);
  capsule.loadFile(path.join(__dirname, "capsule.html"));
}

function updateCapsule() {
  if (!capsule) return;
  const active = [...hands.values()];
  if (!active.length) {
    capsule.hide();
    updateTray();
    return;
  }
  const h = active[active.length - 1];
  const where = h.device === "phone" ? "Nemo is using your phone" : "Nemo is using this computer";
  const label = active.length > 1 ? `${h.label} (+${active.length - 1} more)` : h.label;
  capsule.webContents.send("capsule:update", { label, where });
  const area = screen.getPrimaryDisplay().workArea;
  capsule.setPosition(Math.round(area.x + (area.width - 460) / 2), area.y + 8);
  capsule.showInactive();
  updateTray();
}

async function stopAllHands() {
  await Promise.all([...hands.keys()].map((tid) => post(baseUrl, `/api/threads/${tid}/stop`)));
}

// ---------------------------------------------------------------- tray

function updateTray() {
  if (!tray) return;
  const login = app.getLoginItemSettings().openAtLogin;
  tray.setToolTip(hands.size ? "Nemo — working on your screen" : "Nemo");
  tray.setContextMenu(
    Menu.buildFromTemplate([
      { label: "Open Nemo", click: () => showMain() },
      { label: "New chat", click: () => showMain("#/chat") },
      { type: "separator" },
      { label: "Stop what Nemo is doing on screen", enabled: hands.size > 0, click: stopAllHands },
      { type: "separator" },
      { label: "Start with my computer", type: "checkbox", checked: login, click: (i) => app.setLoginItemSettings({ openAtLogin: i.checked, args: ["--hidden"] }) },
      { label: "Open logs folder", click: () => shell.openPath(app.getPath("logs")) },
      { type: "separator" },
      { label: "Quit Nemo", click: () => { quitting = true; app.quit(); } },
    ]),
  );
}

function createTray() {
  const img = icon("tray.png");
  img.addRepresentation({ scaleFactor: 2, buffer: fs.readFileSync(path.join(ASSETS, "tray@2x.png")) });
  tray = new Tray(img);
  tray.on("click", () => showMain());
  updateTray();
}

// ---------------------------------------------------------------- daemon events

function notify(title, body, hash) {
  if (!Notification.isSupported()) return;
  const n = new Notification({ title, body, icon: icon("icon.png") });
  n.on("click", () => showMain(hash));
  n.show();
}

function windowWatching() {
  return mainWindow && mainWindow.isVisible() && mainWindow.isFocused();
}

function onEvent(ev) {
  switch (ev.type) {
    case "hello":
      hands.clear();
      for (const [tid, h] of Object.entries(ev.hands || {})) hands.set(tid, h);
      updateCapsule();
      break;
    case "hands":
      if (ev.active) hands.set(ev.thread, { device: ev.device, label: ev.label });
      else hands.delete(ev.thread);
      updateCapsule();
      break;
    case "turn_end":
      if (hands.delete(ev.thread)) updateCapsule();
      break;
    case "approval":
      if (!windowWatching()) notify("Nemo needs your OK", ev.approval.reason, `#/chat/${ev.thread}`);
      break;
    case "takeover":
      notify("Your turn", ev.reason, `#/chat/${ev.thread}`);
      break;
    case "notify":
      notify(ev.title || "Nemo", ev.body || "");
      break;
  }
}

// ---------------------------------------------------------------- start-up

async function startDaemon() {
  setupState({ title: "Starting Nemo…", text: "Starting the agent on this computer. The first start can take a minute." });
  daemon = new Daemon({
    appDir: path.join(__dirname, ".."),
    logDir: app.getPath("logs"),
    onExit: (code) => {
      if (quitting) return;
      baseUrl = null;
      mainWindow.loadFile(path.join(__dirname, "setup.html")).then(() =>
        setupState({ title: "Nemo stopped", text: "The agent exited unexpectedly.", error: `Exit code ${code}. Log: ${daemon.logFile}` }),
      );
    },
  });
  try {
    baseUrl = await daemon.start();
  } catch (err) {
    setupState({ title: "Nemo needs Python", text: "", error: err.message, help: err.code === "NO_PYTHON" });
    if (SMOKE) {
      console.error(`SMOKE FAIL: ${err.message}`);
      app.exit(1);
    }
    return;
  }
  if (stopEvents) stopEvents();
  stopEvents = follow(baseUrl, onEvent);
  await mainWindow.loadURL(`${baseUrl}/`);
  if (SMOKE) runSmoke();
}

ipcMain.on("setup:retry", () => startDaemon());
ipcMain.on("capsule:stop", stopAllHands);

// `electron . --smoke[=dir]`: start everything, take screenshots of the window and the capsule, exit.
async function runSmoke() {
  fs.mkdirSync(SMOKE_DIR, { recursive: true });
  await new Promise((r) => setTimeout(r, 3500));
  const title = await mainWindow.webContents.executeJavaScript("document.querySelector('h1, h2')?.textContent || ''");
  fs.writeFileSync(path.join(SMOKE_DIR, "window.png"), (await mainWindow.webContents.capturePage()).toPNG());
  hands.set("smoke", { device: "computer", label: "Tapping “Send” in Mail" });
  updateCapsule();
  await new Promise((r) => setTimeout(r, 1200));
  fs.writeFileSync(path.join(SMOKE_DIR, "capsule.png"), (await capsule.webContents.capturePage()).toPNG());
  const shown = await capsule.webContents.executeJavaScript("(typeof window.capsule) + '|' + document.getElementById('label').textContent");
  console.log(`SMOKE OK url=${baseUrl} owned=${daemon.owned} heading=${JSON.stringify(title)} capsule=${JSON.stringify(shown)} dir=${SMOKE_DIR}`);
  quitting = true;
  daemon.stop();
  app.exit(0);
}

app.whenReady().then(() => {
  if (process.platform === "win32") app.setAppUserModelId("dev.nemo.desktop");
  createMainWindow();
  createCapsule();
  if (!SMOKE) {
    createTray();
    globalShortcut.register("CommandOrControl+Shift+Space", () => (mainWindow.isVisible() && mainWindow.isFocused() ? mainWindow.hide() : showMain()));
    globalShortcut.register("CommandOrControl+Shift+.", stopAllHands);
    if (process.argv.includes("--hidden")) mainWindow.once("ready-to-show", () => mainWindow.hide());
  }
  startDaemon();
});

app.on("second-instance", () => showMain());
app.on("activate", () => showMain());
app.on("before-quit", () => {
  quitting = true;
});
app.on("will-quit", () => {
  globalShortcut.unregisterAll();
  if (stopEvents) stopEvents();
  if (daemon) daemon.stop();
});
app.on("window-all-closed", () => {
  // stay in the tray; quitting is explicit
});
