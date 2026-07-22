/**
 * InputArea — Chat input with auto-growing textarea and send button.
 */

import { KeyboardEvent,useCallback, useRef, useState } from "react";

import { useChatStore } from "../hooks/useChatStore";

export default function InputArea() {
  const [text, setText] = useState("");
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const streaming = useChatStore((s) => s.streaming);
  const sendMessage = useChatStore((s) => s.sendMessage);

  const handleSend = useCallback(() => {
    const trimmed = text.trim();
    if (!trimmed || streaming) return;
    sendMessage(trimmed);
    setText("");
    // Reset textarea height.
    if (textareaRef.current) {
      textareaRef.current.style.height = "auto";
    }
  }, [text, streaming, sendMessage]);

  const handleKeyDown = useCallback(
    (e: KeyboardEvent<HTMLTextAreaElement>) => {
      if (e.key === "Enter" && !e.shiftKey) {
        e.preventDefault();
        handleSend();
      }
    },
    [handleSend]
  );

  const handleInput = useCallback(() => {
    const el = textareaRef.current;
    if (!el) return;
    // Auto-grow up to 6 lines.
    el.style.height = "auto";
    el.style.height = Math.min(el.scrollHeight, 180) + "px";
  }, []);

  return (
    <div style={styles.wrapper}>
      <div style={styles.inputRow}>
        <textarea
          ref={textareaRef}
          value={text}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={handleKeyDown}
          onInput={handleInput}
          placeholder={streaming ? "Waiting for response…" : "Ask about your books…"}
          disabled={streaming}
          style={{
            ...styles.textarea,
            opacity: streaming ? 0.5 : 1,
          }}
          rows={1}
        />
        <button
          onClick={handleSend}
          disabled={streaming || !text.trim()}
          style={{
            ...styles.sendBtn,
            opacity: streaming || !text.trim() ? 0.3 : 1,
            cursor: streaming || !text.trim() ? "default" : "pointer",
          }}
          title="Send (Enter)"
        >
          ↑
        </button>
      </div>
    </div>
  );
}

const styles: Record<string, React.CSSProperties> = {
  wrapper: {
    padding: "12px 20px 16px",
    borderTop: "1px solid #2a2520",
    background: "#1a1814",
  },
  inputRow: {
    display: "flex",
    alignItems: "flex-end",
    gap: 8,
    background: "#0c0c0c",
    border: "1px solid #2a2520",
    borderRadius: 8,
    padding: "8px 12px",
  },
  textarea: {
    flex: 1,
    background: "none",
    border: "none",
    outline: "none",
    color: "#d4c5a9",
    fontSize: 14,
    fontFamily: "'Source Sans 3', sans-serif",
    lineHeight: 1.5,
    resize: "none" as const,
    minHeight: 24,
    maxHeight: 180,
  },
  sendBtn: {
    width: 32,
    height: 32,
    borderRadius: 6,
    border: "none",
    background: "#c17f3e",
    color: "#0c0c0c",
    fontSize: 16,
    fontWeight: 700,
    display: "flex",
    alignItems: "center",
    justifyContent: "center",
    flexShrink: 0,
    transition: "opacity 120ms ease",
  },
};
