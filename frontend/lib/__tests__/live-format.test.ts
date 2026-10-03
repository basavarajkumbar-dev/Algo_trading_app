import { describe, expect, it } from "vitest";

import { canExecute, checkLabel, liveState } from "@/lib/live-format";
import type { LiveStatus, PreTradeCheck } from "@/types/live";

const status = (over: Partial<LiveStatus> = {}): LiveStatus =>
  ({
    configured: true,
    execution_mode: "ZERODHA",
    arming: {
      armed: true,
      armed_by_email: "a@x",
      armed_at: null,
      expires_at: null,
      last_disarm_reason: null,
    },
    auto_trading: false,
    kill_switch: {
      engaged: false,
      reason: null,
      engaged_at: null,
      close_positions: false,
    },
    health: { status: "HEALTHY", age_seconds: 1, checks: [] },
    max_active_positions: 1,
    active_trade: null,
    recent_trades: [],
    unresolved: null,
    arm_blockers: [],
    last_report: null,
    last_error: null,
    limits: {},
    confirmation_phrase: "ARM LIVE TRADING",
    ...over,
  }) as LiveStatus;

const pass: PreTradeCheck[] = [
  { name: "armed", passed: true, detail: "" },
  { name: "valid_stop", passed: true, detail: "" },
];

describe("live state", () => {
  it("is PAPER unless configured, and the kill switch wins", () => {
    expect(liveState(status({ configured: false })).text).toBe("PAPER");
    expect(liveState(status()).text).toBe("LIVE ARMED");
    expect(
      liveState(
        status({
          kill_switch: {
            engaged: true,
            reason: "x",
            engaged_at: null,
            close_positions: false,
          },
        }),
      ).text,
    ).toBe("KILL SWITCH");
    const disarmed = status({
      arming: {
        ...status().arming,
        armed: false,
        last_disarm_reason: "session closed",
      },
    });
    expect(liveState(disarmed).text).toBe("LIVE DISARMED");
  });

  it("offers a live order only when armed and every check passed", () => {
    expect(canExecute(status(), pass)).toBe(true);
    expect(
      canExecute(status(), [
        ...pass,
        { name: "market_open", passed: false, detail: "" },
      ]),
    ).toBe(false);
    expect(
      canExecute(
        status({ arming: { ...status().arming, armed: false } }),
        pass,
      ),
    ).toBe(false);
    expect(canExecute(status({ configured: false }), pass)).toBe(false);
    expect(canExecute(status(), [])).toBe(false);
    expect(
      checkLabel({ name: "no_duplicate_position", passed: true, detail: "" }),
    ).toBe("No duplicate position");
  });
});
