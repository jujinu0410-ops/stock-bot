# Intraday monitoring cards

Only explicit report sessions `1120` and `1335` use the new renderer. `1535`,
unspecified sessions, and V1 retain their original renderer. No scheduler,
dispatch, execution, risk, ATR, or order code is changed.

## Code and data

- `main.run_post_market_analysis`: add read-only intraday rendering arguments.
- `main.verify_pipeline_stock_code_consistency`: require every holding card
  exactly once for the intraday layout, retaining the legacy gate otherwise.
- `GmailNotifier.generate_html_report`: branch by explicit report session.
- `intraday_data.prepare_intraday_holdings`: use the same account response and
  engine result; verify codes and quantities and preserve average-price precision.
- `intraday_data.valuation`: verify the existing engine `pnl_amount` and `pnl_pct`
  against `(current_price - avg_buy_price) * quantity`. Allow only the engine's
  existing integer-won truncation (< 1 won) and two-decimal percent precision.
  Missing values display as unavailable, never invented zero.
- `buy_trailing_dist` / `sell_trailing_dist` come directly from the existing
  `portfolio_reconciler.compute_atr_metrics` output in `held_status`. No ATR
  multiplication or rounding is performed in the renderer. These are the
  monitoring-center 0.6 / 0.4 distances, distinct from V4 order-line parameters
  `buy_rebound_delta` / `profit_trail_delta`, which remain untouched.
- Actual alerts use the monitoring center's `ALERT_LOG!A1:K2001` with raw Sheets
  serial timestamps (the formatted sheet hides time). `held_monitor_5m_v3.gs`
  appends this log after delivery. Filter by KST report day and cutoff, then
  select one latest row per stock. Simultaneous codes on one log row stay
  together. Never use `ALERT_STATE`, Stage gates, or 45m advisory states as alerts.
- Use existing `PortfolioReconciler` authentication and spreadsheet configuration.
  Source lookup failures display `최근 알림 조회 불가`; successful empty results
  display `오늘 신규 신호 없음`.

## Presentation and validation

Navy, table-based inline HTML; one card per holding. DART follows all cards;
existing core-action decisions and concise completed-45m judgments are last.
Correction filings are labeled; matching titles or explicit original receipt
references are grouped, with each receipt and original link preserved.
Source summaries and interpretation are displayed separately and escaped.

`tests/test_intraday_cards.py` covers the session branch, timestamp cutoff,
missing data, P&L mismatch, source ATR distances, suspension, long names,
escaping, DART corrections, preserved decisions, and input immutability.
`scripts/preview_intraday_cards.py` replays the October 19 GCS archives using
their XLSX values, account JSON, full-precision persisted ATR, and actual alert
log. The archive does not preserve the complete Python rendering payload:
strategy fields are reconstructed from saved inputs. It compares the old and
new postmarket routes on identical reconstructed input and verifies that the
legacy renderer source is byte-identical to deployed source. This is distinct
from claiming an exact replay of every byte of the previously sent email.

Private HTML/account inputs remain local; do not commit them to GitHub.
No live mail is sent. Browser previews at 360/390/430/1024 px check clipping and
overflow. Actual received Gmail/mobile-device rendering is not exercised.

## Deployment

`scripts/intraday_cards.Dockerfile` layers only four presentation files over the
immutable currently deployed image digest. The Cloud Build configuration runs
the isolated regression before pushing the runtime image. Its test-only stage
is never deployed. Update only the image of `stockbot-runner`; leave its env,
secrets, arguments, service account, and all scheduler jobs unchanged.

Rollback image:
`asia-northeast3-docker.pkg.dev/stock-monitoring-prod/stockbot-repo/stockbot-cloud-runner@sha256:9378eb6c66bd6e002df939bd13240203519fcc5b53808aa9cd7d0bd15bfb6b34`.
