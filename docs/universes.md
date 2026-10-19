# Stock universes

Official lists are downloaded from niftyindices.com
(`https://www.niftyindices.com/IndexConstituent/<file>.csv`), each verified to
exist on 2026-10-19. There is **no "NIFTY 150" or "NIFTY 250" index**; the
real indices with those sizes are offered under their own names.

| Id | Index | Approx. size |
|---|---|---|
| NIFTY50 | NIFTY 50 | 50 |
| NIFTYNEXT50 | NIFTY Next 50 | 50 |
| NIFTY100 | NIFTY 100 | 100 |
| NIFTY200 | NIFTY 200 | 200 |
| NIFTY500 | NIFTY 500 | 500 |
| MIDCAP100 / MIDCAP150 | NIFTY Midcap 100 / 150 | 100 / 150 |
| SMALLCAP100 / SMALLCAP250 | NIFTY Smallcap 100 / 250 | 100 / 250 |
| LARGEMIDCAP250 | NIFTY LargeMidcap 250 | 250 |
| MICROCAP250 | NIFTY Microcap 250 | 250 |
| BANKNIFTY | NIFTY Bank | 12 |
| PRIVATEBANK | NIFTY Private Bank | 10 |
| NSE_ALL_EQ | Every NSE `EQ` instrument in the Zerodha instrument master (needs a Kite login) | ~2,000+ |

Behaviour (`backend/app/universe/`):

- Several universes can be combined; symbols are de-duplicated in order.
- Downloads are cached on disk (`UNIVERSE_CACHE_DIR`, 7 days), retried 3×,
  and rejected if the size is far from the index's size. On failure the stale
  cache is used, then the built-in NIFTY 50 snapshot; the report says which.
- Eligibility: with real data, a symbol must exist in the Zerodha instrument
  master; with simulated data only NIFTY 50 symbols can be simulated. Excluded
  symbols are listed **with the reason**.
- Limits are configuration, not code: `SCANNER_MAX_SYMBOLS` (200),
  `BACKTEST_MAX_SYMBOLS` (50), hard ceiling 2,000. Symbols over a limit are
  excluded with the reason "over the configured limit of N symbols" — never
  silently dropped. An explicit symbol list over the limit is refused (422).
- Every scan and backtest stores a universe report: requested universes and
  symbols, requested / duplicates / eligible / excluded (with reasons) /
  processed counts, sources and their age, per-symbol bar coverage and missing
  bars (backtests), start/finish time, duration and strategy versions.

API: `GET /api/v1/universes`, `GET /universes/active`, `POST /universes/resolve`,
`GET /universes/{id}`, `POST /universes/{id}/refresh` (operator).

Limitations: index membership is today's list. A backtest over past dates
therefore has **survivorship bias** (stocks that left the index are missing),
and prices are not adjusted for corporate actions.
