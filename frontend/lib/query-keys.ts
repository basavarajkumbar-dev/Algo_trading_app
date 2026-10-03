/** TanStack Query keys. Prefix-based so one invalidation refreshes a family. */
export const qk = {
  tradingStatus: ["trading", "status"] as const,
  tradingRisk: ["trading", "risk"] as const,
  trades: (limit: number) => ["trading", "trades", limit] as const,
  orders: (tradeId: string | null) => ["trading", "orders", tradeId] as const,
  scanner: ["scanner"] as const,
  signals: (params: Record<string, unknown>) => ["signals", params] as const,
  signal: (id: string) => ["signals", "detail", id] as const,
  intelligence: ["market", "intelligence"] as const,
  symbolIntelligence: (symbol: string) => ["market", "intelligence", symbol] as const,
  route: (regime: string, confidence: number) => ["strategies", "route", regime, confidence] as const,
  performance: ["analytics", "performance"] as const,
  components: ["system", "components"] as const,
  events: ["system", "events"] as const,
  systemHealth: ["system", "health"] as const,
  brokerStatus: ["system", "broker"] as const,
  approvals: ["approvals"] as const,
  backtestOptions: ["backtests", "options"] as const,
  backtestRuns: ["backtests", "runs"] as const,
  backtestRun: (id: string) => ["backtests", "run", id] as const,
  backtestTrades: (id: string) => ["backtests", "trades", id] as const,
  backtestEquity: (id: string) => ["backtests", "equity", id] as const,
};

/** What a trading event from the WebSocket makes stale. */
export const TRADING_EVENT_INVALIDATES = [
  ["trading"],
  ["approvals"],
  ["analytics"],
  ["system", "events"],
  ["system", "components"],
] as const;
