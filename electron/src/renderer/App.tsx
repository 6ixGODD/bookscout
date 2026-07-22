/**
 * App — Root component.
 *
 * Connects to the Python WebSocket backend on mount,
 * then renders the main layout (Sidebar + ChatPanel).
 */

import { useEffect, useState } from "react";

import { getWsClient } from "./api/ws-client";
import ChatPanel from "./components/ChatPanel";
import Sidebar from "./components/Sidebar";
import { useChatStore } from "./hooks/useChatStore";

declare global {
  interface Window {
    bookscout: {
      getPythonPort: () => Promise<number>;
    };
  }
}

export default function App() {
  const [ready, setReady] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const setPort = useChatStore((s) => s.setPort);
  const setConnected = useChatStore((s) => s.setConnected);
  const handleWsMessage = useChatStore((s) => s.handleWsMessage);
  const loadSessions = useChatStore((s) => s.loadSessions);
  const loadBooks = useChatStore((s) => s.loadBooks);

  useEffect(() => {
    let client: ReturnType<typeof getWsClient> | null = null;

    async function init() {
      try {
        // Get the Python backend port from the main process.
        const port = await window.bookscout.getPythonPort();
        setPort(port);

        // Connect to the WebSocket server.
        client = getWsClient(port);
        client.onMessage(handleWsMessage);
        await client.connect();
        setConnected(true);

        // Load initial data.
        loadSessions();
        loadBooks();

        setReady(true);
      } catch (err) {
        setError(err instanceof Error ? err.message : "Failed to connect to backend");
      }
    }

    init();

    return () => {
      client?.disconnect();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  if (error) {
    return (
      <div style={styles.error}>
        <h2>Connection Error</h2>
        <p>{error}</p>
        <p style={styles.hint}>Make sure the BookScout Python backend is running.</p>
      </div>
    );
  }

  if (!ready) {
    return (
      <div style={styles.loading}>
        <div style={styles.spinner} />
        <p style={styles.loadingText}>Starting BookScout…</p>
      </div>
    );
  }

  return (
    <div style={styles.layout}>
      <Sidebar />
      <ChatPanel />
    </div>
  );
}

const styles: Record<string, React.CSSProperties> = {
  layout: {
    display: "flex",
    height: "100vh",
    width: "100vw",
    background: "#0c0c0c",
  },
  loading: {
    display: "flex",
    flexDirection: "column",
    alignItems: "center",
    justifyContent: "center",
    height: "100vh",
    background: "#0c0c0c",
    color: "#8b7355",
  },
  spinner: {
    width: 32,
    height: 32,
    border: "3px solid #2a2520",
    borderTopColor: "#c17f3e",
    borderRadius: "50%",
    animation: "spin 0.8s linear infinite",
  },
  loadingText: {
    marginTop: 16,
    fontSize: 14,
    fontFamily: "'Source Sans 3', sans-serif",
  },
  error: {
    display: "flex",
    flexDirection: "column",
    alignItems: "center",
    justifyContent: "center",
    height: "100vh",
    background: "#0c0c0c",
    color: "#c4544a",
    textAlign: "center" as const,
    padding: 32,
  },
  hint: {
    color: "#8b7355",
    marginTop: 8,
    fontSize: 13,
  },
};
