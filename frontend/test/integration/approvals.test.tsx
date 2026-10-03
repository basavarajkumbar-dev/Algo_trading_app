// @vitest-environment jsdom
import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import ApprovalsPage from "@/app/(dashboard)/approvals/page";
import { formatCountdown, secondsLeft } from "@/components/approvals/approval-card";
import { formatPrice } from "@/lib/intelligence-format";
import type { ApprovalList, ApprovalRequest } from "@/types/approvals";

import decidedJson from "../fixtures/approvals-decided.json";
import pendingJson from "../fixtures/approvals-pending.json";
import tradingStatus from "../fixtures/trading-status.json";
import { clone, mockApi, renderWithProviders } from "../render";

const auth = vi.hoisted(() => ({ role: "TRADER" }));
vi.mock("@/components/auth/auth-provider", () => ({
  useAuth: () => ({ accessToken: "token", user: { email: "t@example.com", role: auth.role }, status: "authenticated", logout: vi.fn() }),
}));
vi.mock("next/navigation", () => ({ usePathname: () => "/approvals" }));

const pending = pendingJson as unknown as ApprovalList;
const decided = decidedJson as unknown as ApprovalList;
const approvalStatus = { ...clone(tradingStatus), mode: "APPROVAL", auto_trading: true };

beforeEach(() => {
  auth.role = "TRADER";
});

describe("approvals page (integration)", () => {
  it("shows every field the reviewer needs for each pending trade", async () => {
    mockApi({ "/approvals": pending, "/trading/status": approvalStatus });
    renderWithProviders(<ApprovalsPage />);
    const cards = await screen.findAllByTestId("approval-card");
    expect(cards).toHaveLength(pending.items.length);
    expect(screen.getByTestId("pending-count")).toHaveTextContent(String(pending.items.length));
    const a = pending.items[0] as ApprovalRequest;
    const card = cards[0]!;
    expect(within(card).getByTestId("ap-stock")).toHaveTextContent(a.symbol);
    expect(within(card).getByTestId("ap-direction")).toHaveTextContent(a.direction);
    expect(within(card).getByTestId("ap-entry")).toHaveTextContent(formatPrice(a.entry));
    expect(within(card).getByTestId("ap-stop")).toHaveTextContent(formatPrice(a.stop_loss));
    expect(within(card).getByTestId("ap-target")).toHaveTextContent(formatPrice(a.target));
    expect(within(card).getByTestId("ap-quantity")).toHaveTextContent(String(a.quantity));
    expect(within(card).getByTestId("ap-risk")).toHaveTextContent("₹");
    expect(within(card).getByTestId("ap-rr")).toHaveTextContent(`1 : ${a.reward_risk.toFixed(2)}`);
    expect(within(card).getByTestId("ap-strategy")).toHaveTextContent(/Trend|Breakout|Mean/);
    expect(within(card).getByTestId("ap-score")).toHaveTextContent(a.score!.toFixed(1));
    expect(within(card).getByTestId("ap-regime")).not.toHaveTextContent("—");
    expect(within(card).getByTestId("ap-timestamp")).toHaveTextContent("IST");
    const reasons = within(card).getByTestId("ap-reasons");
    expect(within(reasons).getAllByRole("listitem")).toHaveLength(a.reasons.length);
    expect(within(card).getByTestId("ap-expiry")).toHaveTextContent(/expires in \d+:\d\d/);
    expect(within(card).getByRole("button", { name: /APPROVE TRADE/ })).toBeEnabled();
    expect(within(card).getByRole("button", { name: /REJECT TRADE/ })).toBeEnabled();
  });

  it("approves a trade and reports the paper order", async () => {
    const target = pending.items[0]!;
    let posted = "";
    const fetchMock = mockApi({ "/approvals": pending, "/trading/status": approvalStatus });
    const base = fetchMock.getMockImplementation()!;
    fetchMock.mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
      const path = new URL(String(input)).pathname.replace(/^\/api\/v1/, "");
      if (init?.method === "POST" && path.endsWith("/approve")) {
        posted = path;
        return new Response(JSON.stringify({ ...target, status: "EXECUTED", approved_by_email: "t@example.com" }), { status: 200 });
      }
      return base(input);
    });
    renderWithProviders(<ApprovalsPage />);
    const card = (await screen.findAllByTestId("approval-card"))[0]!;
    fireEvent.click(within(card).getByTestId("ap-approve"));
    await waitFor(() => expect(posted).toBe(`/approvals/${target.id}/approve`));
    expect(await screen.findByTestId("approval-message")).toHaveTextContent(`paper order placed for ${target.symbol}`);
  });

  it("rejecting needs a reason, which is sent", async () => {
    const target = pending.items[1]!;
    let body: { reason?: string } = {};
    const fetchMock = mockApi({ "/approvals": pending, "/trading/status": approvalStatus });
    const base = fetchMock.getMockImplementation()!;
    fetchMock.mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
      const path = new URL(String(input)).pathname.replace(/^\/api\/v1/, "");
      if (init?.method === "POST" && path === `/approvals/${target.id}/reject`) {
        body = JSON.parse(String(init.body));
        return new Response(JSON.stringify({ ...target, status: "REJECTED", reason: body.reason }), { status: 200 });
      }
      return base(input);
    });
    renderWithProviders(<ApprovalsPage />);
    const card = (await screen.findAllByTestId("approval-card"))[1]!;
    fireEvent.click(within(card).getByTestId("ap-reject"));
    const confirm = within(card).getByTestId("ap-confirm-reject");
    expect(confirm).toBeDisabled();
    fireEvent.change(within(card).getByLabelText("Rejection reason"), { target: { value: "Too close to results" } });
    fireEvent.click(confirm);
    await waitFor(() => expect(body.reason).toBe("Too close to results"));
    expect(await screen.findByTestId("approval-message")).toHaveTextContent(`Rejected ${target.symbol}`);
  });

  it("lists decisions with who decided, when and why", async () => {
    mockApi({ "/approvals": decided, "/trading/status": approvalStatus });
    renderWithProviders(<ApprovalsPage />);
    const table = await screen.findByTestId("approval-history");
    const rows = await within(table).findAllByTestId("history-row");
    expect(rows).toHaveLength(decided.items.filter((a) => a.status !== "PENDING").length);
    const rejected = decided.items.find((a) => a.status === "REJECTED")!;
    const row = rows.find((r) => r.textContent?.includes(rejected.symbol))!;
    expect(row).toHaveTextContent("rejected");
    expect(row).toHaveTextContent(rejected.rejected_by_email!);
    expect(row).toHaveTextContent(rejected.reason!);
    const executed = rows.find((r) => r.textContent?.includes("executed"))!;
    expect(within(executed).getByRole("link", { name: "view" })).toBeInTheDocument();
  });

  it("expired requests cannot be approved", async () => {
    const list = clone(pending);
    list.items = list.items.map((a) => ({ ...a, seconds_remaining: 0 }));
    mockApi({ "/approvals": list, "/trading/status": approvalStatus });
    renderWithProviders(<ApprovalsPage />);
    const card = (await screen.findAllByTestId("approval-card"))[0]!;
    expect(within(card).getByTestId("ap-expiry")).toHaveTextContent("expired");
    expect(within(card).getByTestId("ap-approve")).toBeDisabled();
    expect(within(card).getByTestId("ap-reject")).toBeDisabled();
  });

  it("viewers can see requests but not decide or change the mode", async () => {
    auth.role = "VIEWER";
    mockApi({ "/approvals": pending, "/trading/status": approvalStatus });
    renderWithProviders(<ApprovalsPage />);
    const card = (await screen.findAllByTestId("approval-card"))[0]!;
    expect(within(card).getByTestId("ap-approve")).toBeDisabled();
    expect(screen.getByTestId("mode-PAPER")).toBeDisabled();
  });

  it("switches the trading mode", async () => {
    let mode = "";
    const fetchMock = mockApi({ "/approvals": { ...clone(pending), items: [], mode: "PAPER" } });
    const base = fetchMock.getMockImplementation()!;
    fetchMock.mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
      const path = new URL(String(input)).pathname.replace(/^\/api\/v1/, "");
      if (init?.method === "POST" && path === "/trading/mode") {
        mode = JSON.parse(String(init.body)).mode;
        return new Response(JSON.stringify(approvalStatus), { status: 200 });
      }
      return base(input);
    });
    renderWithProviders(<ApprovalsPage />);
    expect(await screen.findByTestId("approvals-mode-note")).toHaveTextContent("PAPER");
    fireEvent.click(screen.getByTestId("mode-APPROVAL"));
    await waitFor(() => expect(mode).toBe("APPROVAL"));
    await waitFor(() => expect(screen.getByTestId("mode-APPROVAL")).toHaveAttribute("aria-checked", "true"));
  });

  it("counts down from the server's remaining time", () => {
    const a = { ...pending.items[0]!, seconds_remaining: 90 };
    expect(secondsLeft(a, 1_000, 31_000)).toBe(60);
    expect(secondsLeft(a, 1_000, 200_000)).toBe(0);
    expect(secondsLeft({ ...a, seconds_remaining: 0 }, 5_000, 4_000)).toBe(0); // clock behind the fetch
    expect(formatCountdown(65.2)).toBe("1:06");
  });
});
