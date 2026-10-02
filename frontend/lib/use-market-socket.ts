"use client";

import { useMarketStore } from "@/lib/stores/market-store";

export type { SocketStatus } from "@/lib/stores/market-store";

/**
 * Live market state from the dashboard's single /ws/market connection
 * (opened once by SocketBridge in the dashboard layout). Kept with its
 * original shape so existing pages need no changes.
 */
export function useMarketSocket() {
  const status = useMarketStore((s) => s.status);
  const snapshots = useMarketStore((s) => s.snapshots);
  const intelligence = useMarketStore((s) => s.intelligence);
  const regimeChanges = useMarketStore((s) => s.regimeChanges);
  return { status, snapshots, intelligence, regimeChanges };
}
