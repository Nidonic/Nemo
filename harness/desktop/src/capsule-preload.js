const { contextBridge, ipcRenderer } = require("electron");

contextBridge.exposeInMainWorld("capsule", {
  onUpdate: (fn) => ipcRenderer.on("capsule:update", (_e, data) => fn(data)),
  stop: () => ipcRenderer.send("capsule:stop"),
});
