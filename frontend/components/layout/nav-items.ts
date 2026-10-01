import type { LucideIcon } from "lucide-react";
import {
  Activity,
  BarChart3,
  CandlestickChart,
  Gauge,
  History,
  LayoutDashboard,
  LineChart,
  ListTree,
  Radar,
  Settings,
  ShieldAlert,
  TestTubeDiagonal,
} from "lucide-react";

export interface NavItem {
  label: string;
  href: string;
  icon: LucideIcon;
}

/**
 * Full nav map from PROJECT_CONTRACT.md / the original brief. Phase 1
 * only implements the Dashboard route — the rest link to placeholder
 * routes that are intentionally not built yet (see roadmap Phase 14).
 * Listing them now keeps the shell's information architecture honest
 * about where the product is going, without pretending those pages
 * exist today.
 */
export const NAV_ITEMS: NavItem[] = [
  { label: "Dashboard", href: "/", icon: LayoutDashboard },
  { label: "Market", href: "/market", icon: LineChart },
  { label: "Price Action", href: "/price-action", icon: CandlestickChart },
  { label: "Scanner", href: "/scanner", icon: Radar },
  { label: "Signals", href: "/signals", icon: Activity },
  { label: "Positions", href: "/positions", icon: ListTree },
  { label: "Trades", href: "/trades", icon: History },
  { label: "Analytics", href: "/analytics", icon: BarChart3 },
  { label: "Strategies", href: "/strategies", icon: TestTubeDiagonal },
  { label: "Backtesting", href: "/backtesting", icon: Gauge },
  { label: "Risk", href: "/risk", icon: ShieldAlert },
  { label: "Settings", href: "/settings", icon: Settings },
];
