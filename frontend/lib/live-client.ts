import { apiGet, apiPost } from "@/lib/api";
import type {
  LiveAuditItem,
  LiveStatus,
  LiveTrade,
  PreTradeReport,
} from "@/types/live";

export const getLiveStatusRequest = () => apiGet<LiveStatus>("/live/status");

export const getLiveAuditRequest = (accessToken: string) =>
  apiGet<{ items: LiveAuditItem[] }>("/live/audit?limit=30", { accessToken });

export const armLiveRequest = (
  confirmation: string,
  password: string,
  accessToken: string,
) =>
  apiPost<LiveStatus>("/live/arm", { confirmation, password }, { accessToken });

export const disarmLiveRequest = (accessToken: string) =>
  apiPost<LiveStatus>("/live/disarm", { reason: "manual" }, { accessToken });

export const liveAutoRequest = (enabled: boolean, accessToken: string) =>
  apiPost<LiveStatus>("/live/auto", { enabled }, { accessToken });

export const livePreflightRequest = (signalId: string, accessToken: string) =>
  apiPost<{ report: PreTradeReport; order_placed: false }>(
    "/live/preflight",
    { signal_id: signalId },
    { accessToken },
  );

export const liveExecuteRequest = (signalId: string, accessToken: string) =>
  apiPost<{
    report: PreTradeReport;
    order_placed: boolean;
    trade: LiveTrade | null;
  }>("/live/execute", { signal_id: signalId }, { accessToken });

export const liveExitRequest = (accessToken: string) =>
  apiPost<LiveStatus>("/live/exit", undefined, { accessToken });

export const killSwitchRequest = (
  reason: string,
  closePositions: boolean | null,
  accessToken: string,
) =>
  apiPost<{ status: LiveStatus }>(
    "/live/kill-switch",
    { reason, close_positions: closePositions },
    { accessToken },
  );

export const resetKillSwitchRequest = (accessToken: string) =>
  apiPost<LiveStatus>("/live/kill-switch/reset", undefined, { accessToken });

export const acknowledgeRecoveryRequest = (note: string, accessToken: string) =>
  apiPost<LiveStatus>("/live/recovery/acknowledge", { note }, { accessToken });
