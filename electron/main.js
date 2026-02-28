const { app, BrowserWindow, ipcMain, dialog, nativeImage } = require("electron");
const path = require("path");

// Disable GPU acceleration on Linux (needed for X11 forwarding and headless).
if (process.platform === "linux") {
  app.disableHardwareAcceleration();
}

const devUrl = process.env.VITE_DEV_SERVER_URL || "http://localhost:5173";
const isDev = Boolean(process.env.VITE_DEV_SERVER_URL) || process.env.ELECTRON_DEV === "1";
const appIconPng = path.join(__dirname, "..", "assets", "icons", "vSNP_Glossy_Helix_1024.jpg");
const appIconIcns = path.join(__dirname, "..", "assets", "icons", "iconsets", "vSNP_Glossy_Helix.icns");
const appTitle = "vSNP GUI";
app.setName(appTitle);
app.name = appTitle;

function createWindow() {
  const win = new BrowserWindow({
    width: 1360,
    height: 900,
    minWidth: 1100,
    minHeight: 720,
    backgroundColor: "#f6f2ec",
    title: appTitle,
    icon: appIconPng,
    webPreferences: {
      preload: path.join(__dirname, "preload.js"),
      contextIsolation: true,
      nodeIntegration: false
    }
  });
  win.on("page-title-updated", (e) => {
    e.preventDefault();
  });
  win.setTitle(appTitle);

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
  if (opts.multiSelect) {
    properties.push("multiSelections");
  }
  const result = await dialog.showOpenDialog({
    title: opts.title || "Select",
    defaultPath: opts.defaultPath || undefined,
    properties
  });
  if (result.canceled) return null;
  if (opts.multiSelect) {
    return result.filePaths;
  }
  return result.filePaths[0] || null;
});

app.whenReady().then(() => {
  app.setName(appTitle);
  if (process.platform === "darwin") {
    const dockPng = nativeImage.createFromPath(appIconPng);
    const dockIcns = nativeImage.createFromPath(appIconIcns);
    if (!dockPng.isEmpty()) {
      app.dock.setIcon(dockPng);
    } else if (!dockIcns.isEmpty()) {
      app.dock.setIcon(dockIcns);
    }
  }
  createWindow();
});

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
