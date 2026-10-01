import type { Config } from "tailwindcss";

// Design tokens for GAINZALGO PRO MAX.
//
// Direction: a professional trading terminal, not a SaaS dashboard and
// not a "crypto casino." The palette takes its cue from monochrome
// amber trading-terminal displays (a real hardware heritage) rather
// than the generic dark-mode-plus-neon-accent look. Green/red are
// reserved strictly for P&L and directional semantics — never used as
// decoration.
const config: Config = {
  darkMode: ["class"],
  content: ["./app/**/*.{ts,tsx}", "./components/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        background: "hsl(var(--background))",
        surface: "hsl(var(--surface))",
        "surface-alt": "hsl(var(--surface-alt))",
        border: "hsl(var(--border))",
        foreground: "hsl(var(--foreground))",
        muted: "hsl(var(--muted-foreground))",
        accent: "hsl(var(--accent))",
        "accent-foreground": "hsl(var(--accent-foreground))",
        profit: "hsl(var(--profit))",
        loss: "hsl(var(--loss))",
        warning: "hsl(var(--warning))",
      },
      fontFamily: {
        // System-stack equivalents chosen deliberately so the build and
        // runtime never depend on fetching web fonts (Docker images
        // must build and run with no external network access).
        sans: [
          "Inter",
          "-apple-system",
          "Segoe UI",
          "Roboto",
          "Helvetica Neue",
          "Arial",
          "sans-serif",
        ],
        mono: [
          "IBM Plex Mono",
          "ui-monospace",
          "SFMono-Regular",
          "Menlo",
          "Consolas",
          "monospace",
        ],
      },
      borderRadius: {
        DEFAULT: "4px",
        sm: "2px",
        md: "4px",
        lg: "6px",
      },
      boxShadow: {
        none: "none",
      },
    },
  },
  plugins: [],
};

export default config;
