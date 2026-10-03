# APPROVAL mode (Phase 12)

```
ALGO -> SIGNAL -> SCORE -> RISK CHECK -> PENDING APPROVAL -> USER REVIEWS
     -> APPROVE / REJECT -> ORDER (paper broker)
```

In APPROVAL mode the algo never trades on its own. Every trade it finds
waits for a person, and an approved trade executes on the **paper
broker** (simulated fills, no real money). There is no live execution in
this phase.

## Turning it on

* At startup: `TRADING_MODE=APPROVAL` (default `PAPER`).
* At runtime: the PAPER / APPROVAL switch on the **Approvals** page, or
  `POST /api/v1/trading/mode {"mode": "APPROVAL"}` (TRADER / ADMIN).
  Every switch is audited (`TRADING_MODE_CHANGED`, before → after). A
  runtime switch lasts until the next restart, which goes back to
  `TRADING_MODE`.
* `LIVE` is refused (422 on the API; `TRADING_MODE=LIVE` makes every risk
  check fail, as before).
* The algo proposes only while it is running (the Auto trading switch on
  Positions). Leaving APPROVAL mode cancels open requests.

## How a request is made

1. **Algo / signal / score.** After each scan, every READY candidate not
   seen before is considered (and a user can ask for one from a signal:
   *Request approval* on the Signals page, or `POST /approvals`).
2. **Risk check.** Every risk check runs as a *preview*: sizing and all
   checks, no order, no state change. A signal the risk check refuses is
   not queued; the algo tries it again on a later scan (e.g. after the
   open trade closes). At most 3 requests are open at a time.
3. **Pending approval.** The request stores what the reviewer sees, once,
   unchanged afterwards: stock, direction, entry, stop loss, target,
   quantity, risk (₹, quantity × stop distance), reward:risk, strategy and
   version, score, market regime and confidence, the reasons, the signal
   time and the request time, plus the risk checks. It expires
   `APPROVAL_TTL_SECONDS` (default 120) after it was made. One open
   request per signal (a partial unique index).

## Deciding

* **APPROVE TRADE** (TRADER / ADMIN) is allowed only for a PENDING
  request that has not expired, whose signal is still ACTIVE, in APPROVAL
  mode, while no paper trade is open (otherwise it stays PENDING and the
  reviewer is told why). Then:
  * `approved_by`, `approved_at` (and an optional note) are stored;
  * every risk check runs **again** at the current price, with the
    approved quantity as an upper limit: more shares than were approved
    are never bought or sold;
  * the paper entry order is placed → **EXECUTED** with the `trade_id`;
    if the risk check now refuses (price moved, limit reached, …) →
    **FAILED** with the reasons. Nothing is retried.
* **REJECT TRADE** (TRADER / ADMIN) needs a reason (3–500 characters):
  `rejected_by`, `rejected_at`, `reason` are stored → **REJECTED**.
* **Expiry.** A sweep every second marks a PENDING request **EXPIRED**
  when its window elapses or its signal expires; approving after expiry
  also marks it EXPIRED. *An expired request is never executed.*
* **Emergency stop** cancels open requests (**CANCELLED**).
* Two people deciding at once: status changes are conditional updates
  (`WHERE status = 'PENDING'`), so only the first succeeds; the other is
  told someone decided first.
* Viewers can see requests but not decide.

### State machine

| From | To | When |
|---|---|---|
| PENDING | APPROVED | a TRADER / ADMIN approves (execution follows at once) |
| PENDING | REJECTED | a TRADER / ADMIN rejects, with a reason |
| PENDING | EXPIRED | window elapsed, or the signal expired |
| PENDING | CANCELLED | emergency stop, or the mode left APPROVAL |
| APPROVED | EXECUTED | risk check passed again; paper entry placed |
| APPROVED | FAILED | risk check refused at execution |

Everything else is refused (`InvalidTransition`); EXECUTED, FAILED,
REJECTED, EXPIRED and CANCELLED are final.

## Safety in depth

* In APPROVAL mode the **risk engine itself** refuses any trade without
  an approval id (`APPROVAL_REQUIRED`), so no path, including the old
  "Paper trade this signal" call, can bypass the queue.
* The paper engine still accepts only PAPER and APPROVAL; LIVE fails every
  check. No broker other than the paper broker receives orders.

## Audit

Every change writes an `audit_logs` row: `APPROVAL_REQUESTED`,
`APPROVAL_APPROVED`, `APPROVAL_REJECTED`, `APPROVAL_EXPIRED`,
`APPROVAL_CANCELLED`, `APPROVAL_EXECUTED`, `APPROVAL_FAILED`, with the
user (or `system`), the status before and after, the trade and the
reason. The same events go to `system_events` (System page event log)
and over the WebSocket, so open dashboards refresh at once.

## Screens

* **Approvals** (sidebar): pending requests with every field above, a
  countdown, APPROVE TRADE / REJECT TRADE (reject asks for the reason),
  the PAPER / APPROVAL switch, and the decision history (who, when, why,
  link to the trade).
* Top bar: mode badge and "N to approve" in APPROVAL mode; the sidebar
  footer shows the mode.
* Signals: *Request approval* replaces *Paper trade this signal* in
  APPROVAL mode.

## Tests

`app/tests/test_approvals.py` (16, Postgres): the algo proposes and
nothing executes; approval executes a paper order and stores
approved_by / approved_at; rejection with reason; expiry by time and by
signal, by sweep and at approval; no execution without approval; refused
signals not queued, then retried on a later scan; the re-run risk check
(FAILED); the approved-quantity cap; a second approval while a trade is
open; one request per signal (also concurrently); two simultaneous
approvals; emergency stop and mode changes cancel; the state machine.
`app/tests/test_approvals_api.py` (4): mode switch and audit, LIVE
refused, roles, reject validation, expired requests over the API.
Frontend: `test/integration/approvals.test.tsx` (8) on captured API
responses; Playwright `e2e/approvals.spec.ts` switches the mode and
reviews and rejects a real queued trade.
