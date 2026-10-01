import type { Metadata } from "next";
import type { ReactNode } from "react";

import { AuthProvider } from "@/components/auth/auth-provider";

import "./globals.css";

export const metadata: Metadata = {
  title: "GAINZALGO PRO MAX",
  description:
    "Modular, explainable, risk-controlled algorithmic intraday trading platform. Runs in PAPER/demo mode with simulated market data.",
};

/**
 * Root layout is intentionally chrome-free: it only sets up global
 * styles and the auth context. The sidebar/header shell lives in
 * app/(dashboard)/layout.tsx so /login can render its own minimal,
 * centered layout instead of the full dashboard frame.
 */
export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="en">
      <body>
        <AuthProvider>{children}</AuthProvider>
      </body>
    </html>
  );
}
