/** Dark trading-terminal palette and spacing. One place for every colour. */

import { Platform } from "react-native";

export const colors = {
  bg: "#0A0E13",
  surface: "#111821",
  surfaceAlt: "#17202B",
  border: "#233041",
  text: "#E6EDF3",
  muted: "#8B98A9",
  faint: "#5B6878",
  accent: "#4C9EFF",
  accentDim: "#1B3A5C",
  profit: "#26C281",
  profitDim: "#103D2C",
  loss: "#F05A5A",
  lossDim: "#4A1C1F",
  warn: "#F2B33D",
  warnDim: "#4A3712",
  info: "#9B8CFF",
} as const;

export const space = { xs: 4, sm: 8, md: 12, lg: 16, xl: 24 } as const;

export const radius = { sm: 6, md: 10, lg: 14 } as const;

export const fonts = {
  mono: Platform.select({ android: "monospace", ios: "Menlo", default: "monospace" }),
} as const;

export type Tone = "neutral" | "good" | "bad" | "warn" | "info" | "accent";

export function toneColors(tone: Tone): { fg: string; bg: string } {
  switch (tone) {
    case "good":
      return { fg: colors.profit, bg: colors.profitDim };
    case "bad":
      return { fg: colors.loss, bg: colors.lossDim };
    case "warn":
      return { fg: colors.warn, bg: colors.warnDim };
    case "info":
      return { fg: colors.info, bg: "#2A2550" };
    case "accent":
      return { fg: colors.accent, bg: colors.accentDim };
    default:
      return { fg: colors.muted, bg: colors.surfaceAlt };
  }
}
