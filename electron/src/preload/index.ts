/**
 * Preload script — secure bridge between main and renderer processes.
 *
 * Exposes a minimal API via contextBridge so the renderer can:
 * - Get the Python backend port
 * - No other Node.js APIs are exposed (security).
 */

import { contextBridge, ipcRenderer } from "electron";

contextBridge.exposeInMainWorld("bookscout", {
  getPythonPort: () => ipcRenderer.invoke("get-python-port"),
});
