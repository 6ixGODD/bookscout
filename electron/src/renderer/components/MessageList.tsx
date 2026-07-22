/**
 * MessageList — Scrollable list of message blocks.
 *
 * Auto-scrolls to bottom when new messages arrive.
 */

import { useEffect,useRef } from "react";

import { useChatStore } from "../hooks/useChatStore";
import AssistantMessage from "./AssistantMessage";
import ToolCallCard from "./ToolCallCard";
import UserMessage from "./UserMessage";

export default function MessageList() {
  const messageBlocks = useChatStore((s) => s.messageBlocks);
  const activeSessionId = useChatStore((s) => s.activeSessionId);
  const bottomRef = useRef<HTMLDivElement>(null);

  // Auto-scroll to bottom on new messages.
  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messageBlocks]);

  // Empty state.
  if (messageBlocks.length === 0) {
    return (
      <div style={styles.empty}>
        <div style={styles.emptyIcon}>📖</div>
        <h2 style={styles.emptyTitle}>BookScout</h2>
        <p style={styles.emptyText}>
          Ask about your books, compile a new one, or search your library.
        </p>
      </div>
    );
  }

  return (
    <div style={styles.list}>
      {messageBlocks.map((block) => {
        switch (block.type) {
          case "user":
            return <UserMessage key={block.id} content={block.content} />;
          case "assistant":
            return (
              <AssistantMessage
                key={block.id}
                content={block.content}
                streaming={block.streaming}
              />
            );
          case "tool_call":
            return (
              <ToolCallCard
                key={block.id}
                data={block.data}
                result={block.result}
              />
            );
          case "status":
            // Status blocks are shown in the header, not inline.
            return null;
          default:
            return null;
        }
      })}
      <div ref={bottomRef} />
    </div>
  );
}

const styles: Record<string, React.CSSProperties> = {
  list: {
    flex: 1,
    overflowY: "auto",
    padding: "20px 24px",
  },
  empty: {
    flex: 1,
    display: "flex",
    flexDirection: "column",
    alignItems: "center",
    justifyContent: "center",
    color: "#8b7355",
  },
  emptyIcon: {
    fontSize: 48,
    marginBottom: 16,
  },
  emptyTitle: {
    fontFamily: "'Playfair Display', Georgia, serif",
    fontSize: 28,
    fontWeight: 600,
    color: "#d4c5a9",
    marginBottom: 8,
  },
  emptyText: {
    fontSize: 14,
    color: "#8b7355",
    maxWidth: 360,
    textAlign: "center" as const,
    lineHeight: 1.6,
  },
};
