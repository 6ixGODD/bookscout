/**
 * BookScout Electron — Main Process
 *
 * Spawns the Python WebSocket backend, creates the browser window,
 * and manages the application lifecycle.
 */

import { app, BrowserWindow } from "electron";
import path from "path";

import { spawnPython, stopPython } from "./python-process";

let mainWindow: BrowserWindow | null = null;
let pythonPort = 18732;

function createWindow() {
  mainWindow = new BrowserWindow({
    width: 1200,
    height: 800,
    minWidth: 800,
    minHeight: 600,
    title: "BookScout",
    backgroundColor: "#0c0c0c",
    titleBarStyle: process.platform === "darwin" ? "hiddenInset" : "default",
    webPreferences: {
      preload: path.join(__dirname, "../preload/index.js"),
      contextIsolation: true,
      nodeIntegration: false,
    },
  });

  // In development, load from Vite dev server.
  if (process.env.VITE_DEV_SERVER_URL) {
    mainWindow.loadURL(process.env.VITE_DEV_SERVER_URL);
  } else {
    mainWindow.loadFile(path.join(__dirname, "../renderer/index.html"));
  }

  mainWindow.on("closed", () => {
    mainWindow = null;
  });
}

app.whenReady().then(async () => {
  // 1. Spawn Python backend.
  pythonPort = await spawnPython();

  // 2. Expose port to renderer via IPC.
  const { ipcMain } = await import("electron");
  ipcMain.handle("get-python-port", () => pythonPort);

  // 3. Create window.
  createWindow();

  app.on("activate", () => {
    if (BrowserWindow.getAllWindows().length === 0) {
      createWindow();
    }
  });
});

app.on("window-all-closed", async () => {
  await stopPython();
  if (process.platform !== "darwin") {
    app.quit();
  }
});

app.on("before-quit", async () => {
  await stopPython();
});
