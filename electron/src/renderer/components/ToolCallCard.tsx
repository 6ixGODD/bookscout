/**
 * ToolCallCard — Collapsible card showing a tool call and its result.
 *
 * Styled as marginalia — a thin amber left border, slightly indented.
 * Feels like annotations a reader would write in the margin of a book.
 */

import type { ToolCallData, ToolResultData } from "@shared/types";
import { useState } from "react";

interface Props {
  data: ToolCallData;
  result?: ToolResultData;
}

export default function ToolCallCard({ data, result }: Props) {
  const [expanded, setExpanded] = useState(false);

  const hasResult = !!result;
  const summary = result?.summary || "";
  const stats = result?.retrieval_stats;
  const statsStr = stats
    ? Object.entries(stats)
        .map(([k, v]) => `${k}=${v}`)
        .join(", ")
    : "";

  return (
    <div style={styles.card}>
      {/* Header — always visible */}
      <button
        style={styles.header}
        onClick={() => setExpanded(!expanded)}
        title={expanded ? "Collapse" : "Expand"}
      >
        <span style={styles.chevron}>{expanded ? "▾" : "▸"}</span>
        <span style={styles.toolName}>{data.tool_name}</span>
        {summary && !expanded && (
          <span style={styles.summary}>{summary}</span>
        )}
        {statsStr && !expanded && (
          <span style={styles.stats}>[{statsStr}]</span>
        )}
        {!hasResult && (
          <span style={styles.running}>●</span>
        )}
      </button>

      {/* Expanded details */}
      {expanded && (
        <div style={styles.details}>
          {data.call_id && (
            <div style={styles.detailRow}>
              <span style={styles.detailLabel}>call_id</span>
              <code style={styles.detailValue}>{data.call_id}</code>
            </div>
          )}
          {result?.arguments && Object.keys(result.arguments).length > 0 && (
            <div style={styles.detailRow}>
              <span style={styles.detailLabel}>args</span>
              <code style={styles.detailValue}>
                {JSON.stringify(result.arguments, null, 2)}
              </code>
            </div>
          )}
          {result?.result_text && (
            <div style={styles.detailRow}>
              <span style={styles.detailLabel}>result</span>
              <span style={styles.detailValue}>{result.result_text.slice(0, 500)}</span>
            </div>
          )}
          {statsStr && (
            <div style={styles.detailRow}>
              <span style={styles.detailLabel}>stats</span>
              <span style={styles.detailValue}>{statsStr}</span>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

const styles: Record<string, React.CSSProperties> = {
  card: {
    marginLeft: 20,
    marginBottom: 8,
    borderLeft: "2px solid #c17f3e",
    borderRadius: "0 6px 6px 0",
    background: "#1a1814",
    overflow: "hidden",
  },
  header: {
    display: "flex",
    alignItems: "center",
    gap: 8,
    width: "100%",
    padding: "8px 12px",
    background: "none",
    border: "none",
    color: "#d4c5a9",
    cursor: "pointer",
    fontFamily: "'Source Sans 3', sans-serif",
    fontSize: 13,
    textAlign: "left" as const,
  },
  chevron: {
    color: "#8b7355",
    fontSize: 11,
    flexShrink: 0,
  },
  toolName: {
    fontFamily: "'Playfair Display', Georgia, serif",
    fontWeight: 600,
    fontSize: 13,
    color: "#7dad6e",
  },
  summary: {
    color: "#a08b6d",
    fontSize: 12,
    overflow: "hidden",
    textOverflow: "ellipsis",
    whiteSpace: "nowrap" as const,
    flex: 1,
    minWidth: 0,
  },
  stats: {
    color: "#6b5f4f",
    fontSize: 11,
    fontFamily: "'JetBrains Mono', monospace",
    flexShrink: 0,
  },
  running: {
    color: "#c17f3e",
    fontSize: 10,
    animation: "pulse 1.5s ease-in-out infinite",
  },
  details: {
    padding: "4px 12px 10px 28px",
    borderTop: "1px solid #2a2520",
  },
  detailRow: {
    display: "flex",
    gap: 8,
    marginBottom: 4,
    alignItems: "flex-start",
  },
  detailLabel: {
    color: "#8b7355",
    fontSize: 11,
    fontFamily: "'JetBrains Mono', monospace",
    flexShrink: 0,
    width: 48,
  },
  detailValue: {
    color: "#a08b6d",
    fontSize: 12,
    fontFamily: "'JetBrains Mono', monospace",
    lineHeight: 1.5,
    wordBreak: "break-all" as const,
  },
};
