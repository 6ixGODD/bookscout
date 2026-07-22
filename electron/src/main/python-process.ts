/**
 * Python process management — spawn, health-check, and stop the backend.
 *
 * The Python backend is started as a child process running
 * ``python -m bookscout.repl ws --port PORT``. We probe the /health
 * endpoint until it responds, then resolve with the chosen port.
 */

import { ChildProcess, spawn } from "child_process";
import http from "http";

const BASE_PORT = 18732;
const MAX_PORT_ATTEMPTS = 9;
const HEALTH_RETRY_MS = 200;
const HEALTH_TIMEOUT_S = 15;

let pythonProcess: ChildProcess | null = null;
let selectedPort = BASE_PORT;

/** Probe the health endpoint on a given port. */
function probeHealth(port: number): Promise<boolean> {
  return new Promise((resolve) => {
    const req = http.get(`http://127.0.0.1:${port}/health`, (res) => {
      res.resume();
      resolve(res.statusCode === 200);
    });
    req.on("error", () => resolve(false));
    req.setTimeout(1000, () => {
      req.destroy();
      resolve(false);
    });
  });
}

/** Wait for the Python backend to become healthy. */
async function waitForHealth(port: number): Promise<boolean> {
  const deadline = Date.now() + HEALTH_TIMEOUT_S * 1000;
  while (Date.now() < deadline) {
    if (await probeHealth(port)) return true;
    await new Promise((r) => setTimeout(r, HEALTH_RETRY_MS));
  }
  return false;
}

/** Find an available port starting from BASE_PORT. */
async function findPort(): Promise<number> {
  for (let offset = 0; offset <= MAX_PORT_ATTEMPTS; offset++) {
    const port = BASE_PORT + offset;
    // If a process is already listening, reuse it.
    if (await probeHealth(port)) {
      return port;
    }
    // Otherwise, we'll try starting on this port.
    return port;
  }
  return BASE_PORT;
}

/** Spawn the Python WebSocket backend and wait for it to be ready. */
export async function spawnPython(): Promise<number> {
  selectedPort = await findPort();

  const isDev = !!process.env.VITE_DEV_SERVER_URL;
  const pythonCmd = process.platform === "win32" ? "python" : "python3";

  const args = ["-m", "bookscout.repl", "ws", "--port", String(selectedPort)];

  pythonProcess = spawn(pythonCmd, args, {
    stdio: ["ignore", "pipe", "pipe"],
    env: { ...process.env },
    detached: false,
  });

  // Log Python stderr for debugging.
  pythonProcess.stderr?.on("data", (data: Buffer) => {
    if (isDev) {
      console.error("[python]", data.toString().trim());
    }
  });

  pythonProcess.on("exit", (code) => {
    if (isDev && code !== 0 && code !== null) {
      console.error(`[python] exited with code ${code}`);
    }
    pythonProcess = null;
  });

  // Wait for the backend to be healthy.
  const healthy = await waitForHealth(selectedPort);
  if (!healthy) {
    console.error("[python] backend did not become healthy in time");
  }

  return selectedPort;
}

/** Gracefully stop the Python backend. */
export async function stopPython(): Promise<void> {
  if (!pythonProcess) return;

  // Send SIGTERM for graceful shutdown (on Unix).
  // On Windows, there's no SIGTERM — just kill.
  if (process.platform !== "win32") {
    pythonProcess.kill("SIGTERM");
    // Wait up to 3 seconds for graceful exit.
    await new Promise((r) => setTimeout(r, 3000));
  }

  // Force-kill if still running.
  if (pythonProcess && !pythonProcess.killed) {
    pythonProcess.kill();
    pythonProcess = null;
  }
}
