/**
 * AssistantMessage — Streaming markdown response from the AI.
 *
 * Renders markdown in real-time using react-markdown.
 * Shows a blinking cursor during streaming.
 */

import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

interface Props {
  content: string;
  streaming: boolean;
}

export default function AssistantMessage({ content, streaming }: Props) {
  return (
    <div style={styles.wrapper}>
      <div style={styles.dot}>●</div>
      <div style={styles.content}>
        {content ? (
          <ReactMarkdown remarkPlugins={[remarkGfm]}>
            {content}
          </ReactMarkdown>
        ) : streaming ? (
          <span style={styles.cursor}>▍</span>
        ) : null}
        {streaming && content && (
          <span style={styles.cursor}>▍</span>
        )}
      </div>
    </div>
  );
}

const styles: Record<string, React.CSSProperties> = {
  wrapper: {
    display: "flex",
    gap: 10,
    marginBottom: 16,
    alignItems: "flex-start",
  },
  dot: {
    color: "#7dad6e",
    fontSize: 12,
    lineHeight: "24px",
    flexShrink: 0,
    marginTop: 2,
  },
  content: {
    flex: 1,
    color: "#d4c5a9",
    fontSize: 14,
    lineHeight: 1.7,
    fontFamily: "'Source Sans 3', sans-serif",
    minWidth: 0,
    // Markdown styling
  },
  cursor: {
    color: "#c17f3e",
    animation: "blink 1s step-end infinite",
    fontWeight: 300,
  },
};
