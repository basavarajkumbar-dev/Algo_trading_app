import { apiGet, apiPost } from "@/lib/api";
import type { ApprovalList, ApprovalRequest } from "@/types/approvals";
import type { TradingStatus } from "@/types/trading";

export const getApprovalsRequest = (status?: string[]) => {
  const q = new URLSearchParams({ limit: "100" });
  for (const s of status ?? []) q.append("status", s);
  return apiGet<ApprovalList>(`/approvals?${q.toString()}`);
};

export const requestApprovalRequest = (signalId: string, accessToken: string) =>
  apiPost<ApprovalRequest>("/approvals", { signal_id: signalId }, { accessToken });

export const approveRequest = (id: string, accessToken: string, note?: string) =>
  apiPost<ApprovalRequest>(`/approvals/${id}/approve`, { note: note || null }, { accessToken });

export const rejectRequest = (id: string, reason: string, accessToken: string) =>
  apiPost<ApprovalRequest>(`/approvals/${id}/reject`, { reason }, { accessToken });

export const setTradingModeRequest = (mode: "PAPER" | "APPROVAL", accessToken: string) =>
  apiPost<TradingStatus>("/trading/mode", { mode }, { accessToken });
