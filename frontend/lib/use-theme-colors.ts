"use client";

import { useEffect, useState } from "react";

export interface ChartColors {
  accent: string;
  profit: string;
  loss: string;
  muted: string;
  border: string;
  surface: string;
  foreground: string;
}

// Fallbacks match app/globals.css; used before mount and in tests.
const FALLBACK: ChartColors = {
  accent: "hsl(38, 75%, 55%)",
  profit: "hsl(152, 60%, 42%)",
  loss: "hsl(357, 70%, 58%)",
  muted: "hsl(215, 12%, 60%)",
  border: "hsl(220, 12%, 20%)",
  surface: "hsl(220, 16%, 10%)",
  foreground: "hsl(210, 20%, 92%)",
};

/** "38 75% 55%" -> "hsl(38, 75%, 55%)" (SVG attributes cannot read CSS variables). */
export function tokenToHsl(token: string): string | null {
  const parts = token.trim().split(/\s+/);
  return parts.length === 3 ? `hsl(${parts.join(", ")})` : null;
}

/** Design tokens resolved to concrete colors for Recharts. */
export function useThemeColors(): ChartColors {
  const [colors, setColors] = useState(FALLBACK);
  useEffect(() => {
    const css = getComputedStyle(document.documentElement);
    const read = (name: string, fallback: string) => tokenToHsl(css.getPropertyValue(name)) ?? fallback;
    setColors({
      accent: read("--accent", FALLBACK.accent),
      profit: read("--profit", FALLBACK.profit),
      loss: read("--loss", FALLBACK.loss),
      muted: read("--muted-foreground", FALLBACK.muted),
      border: read("--border", FALLBACK.border),
      surface: read("--surface", FALLBACK.surface),
      foreground: read("--foreground", FALLBACK.foreground),
    });
  }, []);
  return colors;
}
