# Upgrade report — October 2026

Scope: copy of GainzAlgo Pro Max into `basavarajkumbar-dev/Algo_trading_app`,
fixes for the forensic-audit findings, stock universes, four PDF strategies,
security hardening, an Android app and its build pipeline. The original
project was not modified (see `preservation-report.md`).

## Audit issues: verified, then fixed

| Issue | Verified in the baseline | Fix |
|---|---|---|
| Requested vs processed symbols | The audited "NIFTY 50" run covered 5 symbols; runs were capped at 20 and the report did not state what was requested versus processed. The live scanner used a fixed NIFTY 50 list. | Universe resolver (`app/universe/`) with a stored report for every scan and backtest: requested / duplicates / eligible / excluded with reasons / processed, sources, coverage, missing bars, timings, strategy versions. |
| Limit of 20 | `backtesting/schemas.py: MAX_SYMBOLS = 20` (hard-coded). | Limits are settings (`SCANNER_MAX_SYMBOLS` 200, `BACKTEST_MAX_SYMBOLS` 50; ceiling 2,000). Over-limit symbols are excluded with a reason; an explicit list over the limit is refused (422). |
| Trend Continuation entry (F1) | "Enter beyond the confirmation candle" was not enforced: market at next open in backtests, marketable LIMIT live. | TC 1.1.0 uses a STOP entry: fills only if price trades through the trigger within 2 bars, otherwise expires. Same rule in backtest, paper (SL-M) and live (SL-M / capped LIMIT). |
| R from the planned entry (F2) | R used planned entry → stop. | R = net P&L / ((fill − stop) × quantity); fallback is logged as `R_FALLBACK`. |
| Backtest vs live fill models (F3) | Different entry semantics. | Shared `entry_type` / `entry_valid_bars` carried from strategy → signal → intent → engine / live order manager. |
| 15:00 decisions (minor) | Replay emitted decisions the risk engine always rejects. | Replay skips decisions at or after `TRADING_LAST_ENTRY_TIME` (`REPLAY_VERSION` 2). |
| Swallowed exceptions (minor) | Strategy / scorer errors were dropped silently. | Counted in the replay output and reported as backtest warnings, with symbols that produced no decisions. |
| Insufficient trade history | Few symbols × one position × score ≥ 85 ⇒ very few trades (5 in the audited run). | More symbols are now possible and the universe report shows what was actually processed. Trade count is still a property of the data and thresholds: a small sample remains statistically weak and the report says so. |

Two baseline test failures were fixed (not removed): the Zerodha history
fetcher ignored its injected clock, and a smoke-script test used a fixed date
that had aged out.

## Added

- Universes: `docs/universes.md`. Strategies: `docs/strategies-pdf.md`.
- Security: sign-in required on all data endpoints in production/staging
  (`API_REQUIRE_AUTH`), WebSocket token sent as the first message (never in the
  URL), sign-up closed in production (`ALLOW_REGISTRATION`, accounts via
  `python -m app.scripts.create_user`), the backend refuses to start in
  production with placeholder secrets, `.env.*` ignored, a secret scan in CI.
- Zerodha mobile login: `?client=mobile` returns to `gainzalgo://zerodha`.
- Android app: `docs/mobile-app.md`. Deployment: `docs/deployment.md`.
- CI: `.github/workflows/ci.yml` (backend + web), `android-apk.yml` (APK).

Modes are unchanged: PAPER is the default; APPROVAL needs a person per trade;
live orders need the explicit multi-step activation described in
`docs/live-trading.md`. No app setting can bypass the server-side risk checks.

## Test results (this environment, 2026-10-19)

| Suite | Result |
|---|---|
| Backend pytest (Postgres 16 + Redis) | **791 passed, 0 failed** (8 min); the untouched baseline had 2 failures, both fixed. No test was removed or skipped; existing assertions were changed only where behaviour changed on purpose (TC version, R from the fill, new strategy list, limits) |
| Backend mypy | 17 errors in 6 files — the **same 17 as the untouched baseline** (newer mypy than the project was written for) |
| Backend ruff | only UP042 (`str, Enum` style, the project's convention; 65 in the baseline) |
| Web frontend | tsc OK, lint OK, vitest 98/98 |
| Mobile | tsc OK, eslint 0 warnings, jest 17/17, `expo prebuild` OK (manifest: INTERNET only), `expo export` Android bundle OK; the bundle contains no secret and no development address |



## Known issues and limits

- **The APK was not built here**: this environment cannot reach Google, Maven,
  Gradle or Expo servers. The GitHub Actions workflow builds it; its first run
  has not been observed from here. The app has not been tested on a device.
- Universe lists are today's constituents: backtests over past dates have
  survivorship bias; no corporate-action adjustment.
- Live 60-minute bars may include the truncated 15:15 bar (audit minor, not changed).
- Costs (≈ ₹53 per round trip + slippage) are 0.25–0.6 R per trade at the
  default capital; strategies must clear that before any expectation of profit.
- None of the strategies, old or new, has been shown to be profitable.
- The four PDF strategies use our own default numbers (the PDF gives none).
- No production server was deployed (needs your host and domain).
