/**
 * ChatPanel — Main chat area with message list and input.
 */

import { useEffect,useRef } from "react";

import { useChatStore } from "../hooks/useChatStore";
import InputArea from "./InputArea";
import MessageList from "./MessageList";
import StatusIndicator from "./StatusIndicator";

export default function ChatPanel() {
  const activeSessionId = useChatStore((s) => s.activeSessionId);
  const sessions = useChatStore((s) => s.sessions);
  const streaming = useChatStore((s) => s.streaming);

  const activeSession = sessions.find((s) => s.session_id === activeSessionId);

  return (
    <div style={styles.panel}>
      {/* Header */}
      <div style={styles.header}>
        <span style={styles.sessionName}>
          {activeSession?.name || "BookScout"}
        </span>
        {streaming && <StatusIndicator />}
      </div>

      {/* Messages */}
      <MessageList />

      {/* Input */}
      <InputArea />
    </div>
  );
}

const styles: Record<string, React.CSSProperties> = {
  panel: {
    flex: 1,
    display: "flex",
    flexDirection: "column",
    background: "#0c0c0c",
    minWidth: 0,
  },
  header: {
    display: "flex",
    alignItems: "center",
    justifyContent: "space-between",
    padding: "12px 20px",
    borderBottom: "1px solid #2a2520",
    background: "#1a1814",
  },
  sessionName: {
    fontFamily: "'Playfair Display', Georgia, serif",
    fontSize: 15,
    fontWeight: 600,
    color: "#d4c5a9",
  },
};
