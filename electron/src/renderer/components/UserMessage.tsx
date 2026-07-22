/**
 * UserMessage — User chat bubble.
 *
 * Warm dark background with a left accent bar.
 */

interface Props {
  content: string;
}

export default function UserMessage({ content }: Props) {
  return (
    <div style={styles.wrapper}>
      <div style={styles.bubble}>
        {content}
      </div>
    </div>
  );
}

const styles: Record<string, React.CSSProperties> = {
  wrapper: {
    display: "flex",
    justifyContent: "flex-end",
    marginBottom: 16,
  },
  bubble: {
    maxWidth: "75%",
    padding: "10px 16px",
    background: "#2a2520",
    borderLeft: "3px solid #c17f3e",
    borderRadius: "6px",
    color: "#d4c5a9",
    fontSize: 14,
    lineHeight: 1.6,
    fontFamily: "'Source Sans 3', sans-serif",
  },
};
