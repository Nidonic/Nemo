// Finds a Python that has Nemo installed, starts `nemo serve` on a free local port, and stops it on quit.
// If a Nemo daemon is already answering on the default port (or NEMO_DESKTOP_URL), that one is used instead,
// so two daemons never run against the same data folder.

const { spawn, spawnSync } = require("node:child_process");
const fs = require("node:fs");
const http = require("node:http");
const net = require("node:net");
const path = require("node:path");

const IS_WIN = process.platform === "win32";
const DEFAULT_URL = "http://127.0.0.1:7860";

function health(url, timeoutMs = 1500) {
  return new Promise((resolve) => {
    const req = http.get(`${url}/v1/health`, { timeout: timeoutMs }, (res) => {
      let body = "";
      res.on("data", (c) => (body += c));
      res.on("end", () => {
        try {
          resolve(res.statusCode === 200 && JSON.parse(body).ok === true);
        } catch {
          resolve(false);
        }
      });
    });
    req.on("error", () => resolve(false));
    req.on("timeout", () => {
      req.destroy();
      resolve(false);
    });
  });
}

function freePort() {
  return new Promise((resolve, reject) => {
    const srv = net.createServer();
    srv.unref();
    srv.on("error", reject);
    srv.listen(0, "127.0.0.1", () => {
      const { port } = srv.address();
      srv.close(() => resolve(port));
    });
  });
}

function hasNemo(python) {
  try {
    const r = spawnSync(python, ["-c", "import nemo, sys; print(nemo.__version__)"], { timeout: 60000, windowsHide: true });
    return r.status === 0 ? String(r.stdout).trim() : null;
  } catch {
    return null;
  }
}

/** Candidate interpreters, most specific first. */
function candidates(appDir) {
  const list = [];
  if (process.env.NEMO_PYTHON) list.push(process.env.NEMO_PYTHON);
  // a bundled runtime, if a packaged build ships one under resources/python
  const res = process.resourcesPath || "";
  list.push(path.join(res, "python", IS_WIN ? "python.exe" : "bin/python3"));
  // the repository's virtualenv when running from source (harness/desktop → repo root)
  const repo = path.resolve(appDir, "..", "..");
  list.push(path.join(repo, ".venv", IS_WIN ? "Scripts/python.exe" : "bin/python"));
  list.push(...(IS_WIN ? ["py", "python", "python3"] : ["python3", "python"]));
  return list.filter((p) => !path.isAbsolute(p) || fs.existsSync(p));
}

class Daemon {
  constructor({ appDir, logDir, onExit }) {
    this.appDir = appDir;
    this.logDir = logDir;
    this.onExit = onExit;
    this.proc = null;
    this.url = null;
    this.owned = false;
    this.python = null;
    this.version = null;
    this.logFile = path.join(logDir, "daemon.log");
  }

  async start() {
    const existing = process.env.NEMO_DESKTOP_URL || DEFAULT_URL;
    if (await health(existing)) {
      this.url = existing;
      return this.url;
    }
    for (const python of candidates(this.appDir)) {
      const version = hasNemo(python);
      if (version) {
        this.python = python;
        this.version = version;
        break;
      }
    }
    if (!this.python) {
      const err = new Error("No Python with Nemo installed was found.");
      err.code = "NO_PYTHON";
      throw err;
    }
    const port = await freePort();
    fs.mkdirSync(this.logDir, { recursive: true });
    const log = fs.openSync(this.logFile, "a");
    fs.writeSync(log, `\n--- ${new Date().toISOString()} starting ${this.python} -m nemo serve --port ${port}\n`);
    this.proc = spawn(this.python, ["-m", "nemo", "serve", "--no-browser", "--port", String(port)], {
      env: { ...process.env, PYTHONIOENCODING: "utf-8", PYTHONUNBUFFERED: "1" },
      stdio: ["ignore", log, log],
      windowsHide: true,
    });
    this.owned = true;
    this.proc.on("exit", (code) => {
      this.proc = null;
      if (this.onExit) this.onExit(code);
    });
    this.url = `http://127.0.0.1:${port}`;
    const deadline = Date.now() + 120000;
    while (Date.now() < deadline) {
      if (!this.proc) throw new Error(`Nemo exited while starting. See ${this.logFile}.`);
      if (await health(this.url)) return this.url;
      await new Promise((r) => setTimeout(r, 400));
    }
    this.stop();
    throw new Error(`Nemo did not come up within two minutes. See ${this.logFile}.`);
  }

  stop() {
    if (!this.proc || !this.owned) return;
    const pid = this.proc.pid;
    this.onExit = null;
    if (IS_WIN) spawnSync("taskkill", ["/PID", String(pid), "/T", "/F"], { windowsHide: true });
    else this.proc.kill("SIGTERM");
    this.proc = null;
  }
}

module.exports = { Daemon, health };
