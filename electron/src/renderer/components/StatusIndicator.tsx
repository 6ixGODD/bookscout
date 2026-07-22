/**
 * StatusIndicator — Spinner + phase text shown in the header during streaming.
 */

import { useChatStore } from "../hooks/useChatStore";

export default function StatusIndicator() {
  const streaming = useChatStore((s) => s.streaming);

  if (!streaming) return null;

  return (
    <div style={styles.wrapper}>
      <div style={styles.spinner} />
      <span style={styles.text}>Thinking…</span>
    </div>
  );
}

const styles: Record<string, React.CSSProperties> = {
  wrapper: {
    display: "flex",
    alignItems: "center",
    gap: 8,
  },
  spinner: {
    width: 14,
    height: 14,
    border: "2px solid #2a2520",
    borderTopColor: "#c17f3e",
    borderRadius: "50%",
    animation: "spin 0.8s linear infinite",
  },
  text: {
    fontSize: 12,
    color: "#8b7355",
    fontFamily: "'Source Sans 3', sans-serif",
  },
};
