"use client";

import type { ReactNode } from "react";

import { useAuth } from "@/components/auth/auth-provider";
import { Header } from "@/components/layout/header";
import { Sidebar } from "@/components/layout/sidebar";

/**
 * `middleware.ts` already redirects unauthenticated requests to /login
 * before this ever renders. This guard is defense-in-depth for the
 * client-side navigation case (e.g. the access token expiring while the
 * tab is open) — it shows a loading state during hydration and falls
 * back to a plain message rather than flashing dashboard content if,
 * for any reason, this layout renders while unauthenticated.
 */
export default function DashboardLayout({ children }: { children: ReactNode }) {
  const { status } = useAuth();

  if (status === "loading") {
    return (
      <div className="flex h-screen items-center justify-center bg-background text-sm text-muted">
        Loading…
      </div>
    );
  }

  if (status === "unauthenticated") {
    return (
      <div className="flex h-screen items-center justify-center bg-background text-sm text-muted">
        Redirecting to login…
      </div>
    );
  }

  return (
    <div className="flex h-screen overflow-hidden">
      <Sidebar />
      <div className="flex flex-1 flex-col overflow-hidden">
        <Header />
        <main className="flex-1 overflow-y-auto bg-background p-6">{children}</main>
      </div>
    </div>
  );
}
