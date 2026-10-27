// Follows the daemon's Server-Sent Events stream from the main process, reconnecting when it drops.

const http = require("node:http");

function follow(url, onEvent, onState = () => {}) {
  let req = null;
  let closed = false;
  let retry = 1000;

  const connect = () => {
    if (closed) return;
    req = http.get(`${url}/api/events`, { headers: { Accept: "text/event-stream" } }, (res) => {
      if (res.statusCode !== 200) {
        res.resume();
        return schedule();
      }
      retry = 1000;
      onState(true);
      res.setEncoding("utf8");
      let buf = "";
      res.on("data", (chunk) => {
        buf += chunk;
        let at;
        while ((at = buf.indexOf("\n\n")) >= 0) {
          const block = buf.slice(0, at);
          buf = buf.slice(at + 2);
          const data = block
            .split("\n")
            .filter((l) => l.startsWith("data:"))
            .map((l) => l.slice(5).trimStart())
            .join("\n");
          if (!data) continue;
          try {
            onEvent(JSON.parse(data));
          } catch {
            /* a malformed event is skipped */
          }
        }
      });
      res.on("end", schedule);
      res.on("error", schedule);
    });
    req.on("error", schedule);
  };

  const schedule = () => {
    onState(false);
    if (closed) return;
    setTimeout(connect, retry);
    retry = Math.min(retry * 2, 15000);
  };

  connect();
  return () => {
    closed = true;
    if (req) req.destroy();
  };
}

function post(url, path, body = {}) {
  return new Promise((resolve) => {
    const data = JSON.stringify(body);
    const req = http.request(`${url}${path}`, { method: "POST", headers: { "Content-Type": "application/json", "Content-Length": Buffer.byteLength(data) } }, (res) => {
      res.resume();
      res.on("end", () => resolve(res.statusCode));
    });
    req.on("error", () => resolve(0));
    req.end(data);
  });
}

module.exports = { follow, post };
