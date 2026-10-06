# Mobile V8 Runtime

This directory is the canonical source for the Mobile V8 and portfolio-special Cloud Run runtime.

## Source of truth

- Canonical repository: `jujinu0410-ops/stock-bot`
- Runtime source: `mobile_v8/`
- Shared V8 adapter dependency: `daily_v8/`
- Private V8 engine: cloned at deploy time from `jujinu0410-ops/stock-analysis-system-v8`
- Pinned engine SHA is defined in `deploy_portfolio_special.ps1`
- Cloud Build source tarballs are deployment artifacts/recovery evidence only. Do not treat them as the editable source of truth.

## Portfolio-special deployment

From the repository root on an authenticated deployment workstation:

```powershell
powershell.exe -ExecutionPolicy Bypass -File .\mobile_v8\deploy_portfolio_special.ps1 -PreflightOnly
powershell.exe -ExecutionPolicy Bypass -File .\mobile_v8\deploy_portfolio_special_safe.ps1
```

The safe deploy preserves the weekday 16:00 KST scheduler and the fixed Drive bridge file used by the production job.

## Financial profiles

`GENERAL` keeps the original strict parity requirements.

`BANK_HOLDING` is intentionally narrow and currently applies only when DART `induty_code == "64992"` (financial holding companies). It does not synthesize a manufacturing-style Revenue value.

BANK_HOLDING canonical core:
- operating income
- profit/loss
- total assets
- total equity
- equity/assets ratio

Revenue and operating cash flow remain visible as raw evidence when available, but are not core completeness requirements for this profile. General-company F-score/growth/cash-flow/debt score fields are invalidated rather than reused.

Do not hard-code individual stock codes such as 316140 as exceptions. Expand industry coverage only through explicit, tested financial profiles.

## Regression checks

Before deployment:

```powershell
python -m compileall -q daily_v8 mobile_v8
python -m pytest -q tests\test_bank_holding_profile.py tests\test_dart_data_integrity.py tests\test_cloud_run_phase1.py tests\test_bollinger_atr_strategy.py
```

The BANK_HOLDING regression test must prove both:
1. financial holding data with missing manufacturing-style Revenue can become `BANK_SYNCED`; and
2. GENERAL companies with missing Revenue still fail with `latest_core_missing`.

## Production safety

- Do not relax GENERAL parity validation to solve a financial-sector data shape.
- Do not fill MISSING values with estimates or zeroes.
- Keep stock-specific analysis failures isolated so one issuer cannot kill the full daily V8 run.
- After deployment, verify the Cloud Run image digest, scheduler state, and one real execution before considering the change operational.
