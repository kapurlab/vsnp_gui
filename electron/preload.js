const { contextBridge, ipcRenderer } = require("electron");

contextBridge.exposeInMainWorld("vsnp", {
  selectPath: (opts) => ipcRenderer.invoke("select-path", opts)
});
