/**
 * Sidebar — Sessions list, books library, and commands.
 *
 * Warm dark surface with the Playfair Display serif for session names.
 */

import { useChatStore } from "../hooks/useChatStore";

export default function Sidebar() {
  const sessions = useChatStore((s) => s.sessions);
  const activeSessionId = useChatStore((s) => s.activeSessionId);
  const books = useChatStore((s) => s.books);
  const selectSession = useChatStore((s) => s.selectSession);
  const createSession = useChatStore((s) => s.createSession);
  const deleteSession = useChatStore((s) => s.deleteSession);

  return (
    <div style={styles.sidebar}>
      {/* App title */}
      <div style={styles.titleArea}>
        <h1 style={styles.title}>BookScout</h1>
      </div>

      {/* New session button */}
      <button style={styles.newBtn} onClick={() => createSession()}>
        + New Session
      </button>

      {/* Sessions */}
      <div style={styles.section}>
        <div style={styles.sectionLabel}>Sessions</div>
        {sessions.map((session) => (
          <div
            key={session.session_id}
            style={{
              ...styles.sessionItem,
              background:
                session.session_id === activeSessionId
                  ? "#2a2520"
                  : "transparent",
            }}
            onClick={() => selectSession(session.session_id)}
          >
            <span style={styles.sessionName}>{session.name}</span>
            <span style={styles.sessionTurns}>{session.turn_count} turns</span>
          </div>
        ))}
        {sessions.length === 0 && (
          <div style={styles.emptyHint}>No sessions yet</div>
        )}
      </div>

      {/* Library */}
      <div style={styles.section}>
        <div style={styles.sectionLabel}>Library</div>
        {books.map((book) => (
          <div key={book.id} style={styles.bookItem}>
            <span style={styles.bookIcon}>📖</span>
            <span style={styles.bookTitle}>{book.title}</span>
          </div>
        ))}
        {books.length === 0 && (
          <div style={styles.emptyHint}>No books compiled yet</div>
        )}
      </div>

      {/* Commands */}
      <div style={styles.section}>
        <div style={styles.sectionLabel}>Commands</div>
        {["/compile", "/search", "/library", "/read", "/index"].map((cmd) => (
          <div key={cmd} style={styles.commandItem}>
            <code style={styles.commandCode}>{cmd}</code>
          </div>
        ))}
      </div>
    </div>
  );
}

const styles: Record<string, React.CSSProperties> = {
  sidebar: {
    width: 260,
    flexShrink: 0,
    background: "#1a1814",
    borderRight: "1px solid #2a2520",
    display: "flex",
    flexDirection: "column",
    overflowY: "auto",
  },
  titleArea: {
    padding: "16px 16px 8px",
  },
  title: {
    fontFamily: "'Playfair Display', Georgia, serif",
    fontSize: 20,
    fontWeight: 600,
    color: "#d4c5a9",
    margin: 0,
  },
  newBtn: {
    margin: "4px 16px 12px",
    padding: "8px 12px",
    background: "#2a2520",
    border: "1px solid #3a3530",
    borderRadius: 6,
    color: "#c17f3e",
    fontSize: 13,
    fontFamily: "'Source Sans 3', sans-serif",
    cursor: "pointer",
    textAlign: "left" as const,
  },
  section: {
    padding: "8px 0",
    borderTop: "1px solid #2a2520",
  },
  sectionLabel: {
    padding: "4px 16px 6px",
    fontSize: 11,
    fontWeight: 600,
    color: "#6b5f4f",
    textTransform: "uppercase" as const,
    letterSpacing: "0.05em",
  },
  sessionItem: {
    display: "flex",
    alignItems: "center",
    justifyContent: "space-between",
    padding: "6px 16px",
    cursor: "pointer",
    transition: "background 120ms ease",
  },
  sessionName: {
    fontFamily: "'Playfair Display', Georgia, serif",
    fontSize: 13,
    fontWeight: 400,
    color: "#d4c5a9",
    overflow: "hidden",
    textOverflow: "ellipsis",
    whiteSpace: "nowrap" as const,
    flex: 1,
    minWidth: 0,
  },
  sessionTurns: {
    fontSize: 11,
    color: "#6b5f4f",
    fontFamily: "'JetBrains Mono', monospace",
    flexShrink: 0,
    marginLeft: 8,
  },
  bookItem: {
    display: "flex",
    alignItems: "center",
    gap: 8,
    padding: "4px 16px",
  },
  bookIcon: {
    fontSize: 14,
    flexShrink: 0,
  },
  bookTitle: {
    fontSize: 13,
    color: "#a08b6d",
    overflow: "hidden",
    textOverflow: "ellipsis",
    whiteSpace: "nowrap" as const,
  },
  commandItem: {
    padding: "3px 16px",
  },
  commandCode: {
    fontSize: 12,
    color: "#8b7355",
    background: "none",
    padding: 0,
  },
  emptyHint: {
    padding: "4px 16px",
    fontSize: 12,
    color: "#6b5f4f",
    fontStyle: "italic",
  },
};
