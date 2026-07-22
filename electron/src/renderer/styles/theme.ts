/**
 * BookScout Design Tokens
 *
 * Palette grounded in book materials: ink, vellum, sepia, parchment, leather, amber.
 * Deliberately warm-dark — not the cool-blue-dark of generic AI chat apps.
 * The warmth says "books" not "terminal."
 */

export const theme = {
  color: {
    // Backgrounds
    ink: "#0c0c0c",
    vellumDark: "#1a1814",
    vellumMid: "#221f1a",
    sepiaBorder: "#2a2520",
    sepiaLight: "#3a3530",

    // Text
    parchment: "#d4c5a9",
    parchmentDim: "#a89878",
    leather: "#8b7355",
    leatherLight: "#a08b6d",

    // Accent
    amber: "#c17f3e",
    amberBright: "#d4943f",
    amberDim: "#8a5a2a",

    // Semantic
    toolCall: "#7dad6e",       // Muted sage green — like aged ink
    toolResult: "#6b8f5e",     // Slightly darker sage
    error: "#c4544a",          // Warm red — like a red pencil mark
    thinking: "#6b5f4f",       // Warm gray — like pencil lead

    // Utility
    white: "#f0e6d2",          // Warm white
    black: "#0c0c0c",
  },

  font: {
    display: "'Playfair Display', Georgia, serif",
    body: "'Source Sans 3', 'Source Sans Pro', system-ui, sans-serif",
    mono: "'JetBrains Mono', 'Fira Code', monospace",
  },

  size: {
    sidebarWidth: "260px",
    inputMaxHeight: "180px",
    borderRadius: "6px",
    borderRadiusSm: "3px",
  },

  shadow: {
    card: "0 1px 3px rgba(0,0,0,0.4)",
    elevated: "0 4px 12px rgba(0,0,0,0.5)",
  },

  transition: {
    fast: "120ms ease",
    normal: "200ms ease",
    slow: "300ms ease",
  },
} as const;

export type Theme = typeof theme;
