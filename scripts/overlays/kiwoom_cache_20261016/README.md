# P1: Cloud Run Kiwoom / completed-candle caching (2026-10-16)

## Verified existing behavior
Production `services.unified_scan` already had keyed, process-local, thread-locked caches:
- OAuth token: 1,200s (20min), **unchanged**.
- Quote: 15s, originally indiscriminately reused for individual ticker and market/sector ETF.
- Daily Naver history: 240s (4min), **unchanged**.
- Complete 45m history: 180s (3min), before this patch.

## Actual changes
- **Individual ticker current price:** request a fresh Kiwoom `ka10001` quote for every evaluation. Avoid hiding a fresh STOP/BUY price behind a 15s-old quote. A verified quote may still seed the **context** cache for a matching sector/market code.
- **Shared market/sector ETF:** preserve 15s verified context quote cache (per-worker, keyed by symbol). Never cache unavailable quotes as successful context, and never invent price data.
- **Completed 45m candles:** valid `get_strictly_sliced_45m_df` outputs cached 900s for an exact `ticker + completed cutoff`; invalid/missing candles are **not** cached, and the next cutoff automatically causes a fresh request. All existing validation and >=28-complete-bar rules remain unchanged.
- **Memory:** expire old entries selectively rather than clearing every warm cache, retain at most ~1200 entries, reclaim unused per-key locks with weak references.
- **Unchanged:** per-ticker 1-minute Kiwoom chart requests, API routes, decision thresholds, JEV, 30-day candidate tracking, failure retries, Gmail state/dedup, order APIs.

## Verification
- Production-derived image built with the exact digest in the Dockerfile; **4 isolated unit tests passed in the container** (token single-flight, ticker freshness/context sharing, 45m valid hit/new-cutoff/TTL, negative results and eviction).
- Production revision **`youtube-signal-service-00030-k2c`**, 100% service traffic and `/health` healthy on 2026-10-16.
- Live calls / mail / trading **not** forced during postmarket testing. Check the next session for reduced 45m source reads and absence of Kiwoom rate-limit errors; cache effectiveness across Cloud Run instances is not guaranteed.

## Rollback
`gcloud run services update-traffic youtube-signal-service --project=stock-monitoring-prod --region=asia-northeast3 --to-revisions=youtube-signal-service-00029-v5w=100`

## Build
Run from repository root:
`gcloud builds submit . --project=stock-monitoring-prod --region=asia-northeast3 --config=scripts/overlays/kiwoom_cache_20261016/cloudbuild.yaml`

This is a reproducible overlay on the verified live image. It is **not** an instruction to replace the deployed source with the outdated main-branch service code.
