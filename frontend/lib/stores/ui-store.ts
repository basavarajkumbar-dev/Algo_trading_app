import { create } from "zustand";
import { createJSONStorage, persist } from "zustand/middleware";

interface UiState {
  /** ISO time the notifications menu was last opened (per browser). */
  notificationsSeenAt: string | null;
  markNotificationsSeen: (at?: string) => void;
  /** Signal shown in the dashboard's signal panel; null = the top-ranked one. */
  dashboardSignalKey: string | null;
  setDashboardSignalKey: (key: string | null) => void;
}

// Per-viewer conveniences only. Storage may be unavailable (private
// windows, tests); zustand's persist then just keeps state in memory.
export const useUiStore = create<UiState>()(
  persist(
    (set) => ({
      notificationsSeenAt: null,
      markNotificationsSeen: (at) => set({ notificationsSeenAt: at ?? new Date().toISOString() }),
      dashboardSignalKey: null,
      setDashboardSignalKey: (key) => set({ dashboardSignalKey: key }),
    }),
    {
      name: "gainzalgo-ui",
      storage: createJSONStorage(() => localStorage),
      partialize: (state) => ({ notificationsSeenAt: state.notificationsSeenAt }),
    }
  )
);

/** Events newer than the last time the menu was opened. */
export function countUnread(createdAts: string[], seenAt: string | null): number {
  if (!seenAt) return createdAts.length;
  const seen = Date.parse(seenAt);
  return createdAts.filter((t) => Date.parse(t) > seen).length;
}
