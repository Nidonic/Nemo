const { contextBridge, ipcRenderer } = require("electron");

contextBridge.exposeInMainWorld("setup", {
  onState: (fn) => ipcRenderer.on("setup:state", (_e, data) => fn(data)),
  retry: () => ipcRenderer.send("setup:retry"),
});
