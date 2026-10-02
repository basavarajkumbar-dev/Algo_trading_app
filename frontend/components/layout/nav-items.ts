import type { LucideIcon } from "lucide-react";
import {
  Activity,
  BarChart3,
  Gauge,
  History,
  LayoutDashboard,
  LineChart,
  ListTree,
  MonitorCheck,
  Radar,
  Settings,
  ShieldAlert,
  TestTubeDiagonal,
} from "lucide-react";

export interface NavItem {
  label: string;
  href: string;
  icon: LucideIcon;
  /** Shown next to the label when the page exists but its engine does not yet. */
  tag?: string;
}

/** Sidebar, in the order of the Phase 9 brief. Price Action lives under Market. */
export const NAV_ITEMS: NavItem[] = [
  { label: "Dashboard", href: "/", icon: LayoutDashboard },
  { label: "Market", href: "/market", icon: LineChart },
  { label: "Scanner", href: "/scanner", icon: Radar },
  { label: "Signals", href: "/signals", icon: Activity },
  { label: "Positions", href: "/positions", icon: ListTree },
  { label: "Trades", href: "/trades", icon: History },
  { label: "Analytics", href: "/analytics", icon: BarChart3 },
  { label: "Strategies", href: "/strategies", icon: TestTubeDiagonal },
  { label: "Backtesting", href: "/backtesting", icon: Gauge, tag: "planned" },
  { label: "Risk", href: "/risk", icon: ShieldAlert },
  { label: "System", href: "/system", icon: MonitorCheck },
  { label: "Settings", href: "/settings", icon: Settings },
];

/** Routes that highlight a nav item other than their own path. */
export const NAV_ALIASES: Record<string, string> = {
  "/price-action": "/market",
};

export function activeNavHref(pathname: string): string {
  if (NAV_ALIASES[pathname]) return NAV_ALIASES[pathname];
  const match = NAV_ITEMS.filter((i) => i.href !== "/" && pathname.startsWith(i.href));
  return match[0]?.href ?? (pathname === "/" ? "/" : pathname);
}
