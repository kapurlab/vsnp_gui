const { app, BrowserWindow, ipcMain, dialog } = require("electron");
const path = require("path");

const devUrl = process.env.VITE_DEV_SERVER_URL || "http://localhost:5173";
const isDev = Boolean(process.env.VITE_DEV_SERVER_URL) || process.env.ELECTRON_DEV === "1";

function createWindow() {
  const win = new BrowserWindow({
    width: 1360,
    height: 900,
    minWidth: 1100,
    minHeight: 720,
    backgroundColor: "#f6f2ec",
    webPreferences: {
      preload: path.join(__dirname, "preload.js"),
      contextIsolation: true,
      nodeIntegration: false
    }
  });

  if (isDev) {
    win.loadURL(devUrl);
  } else {
    win.loadFile(path.join(__dirname, "..", "frontend", "dist", "index.html"));
  }
}

ipcMain.handle("select-path", async (_event, opts = {}) => {
  const properties = [];
  if (opts.kind === "file") {
    properties.push("openFile");
  } else {
    properties.push("openDirectory");
  }
  const result = await dialog.showOpenDialog({
    title: opts.title || "Select",
    defaultPath: opts.defaultPath || undefined,
    properties
  });
  if (result.canceled) return null;
  return result.filePaths[0] || null;
});

app.whenReady().then(createWindow);

app.on("activate", () => {
  if (BrowserWindow.getAllWindows().length === 0) {
    createWindow();
  }
});

app.on("window-all-closed", () => {
  if (process.platform !== "darwin") {
    app.quit();
  }
});
