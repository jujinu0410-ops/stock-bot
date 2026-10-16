# 45m VWAP sparse completed-candle hotfix — 2026-10-16

## Incident / evidence
- YouTube candidate `DISPATCH_LOG`: `45M_VWAP_UNAVAILABLE` observed in 108 of the latest 171 review rows (a sample, not all candidates).
- The running `youtube-signal-service` image was digest `sha256:116536310e6faa4a57f4322e88c058965070e158dc4ba36581827a86dedf9514`, tag `unified-three-lists-v2-20261013`.
- Its 2,158-line production service differs from the old source in GitHub main. **Do not redeploy the old service from main as a replacement**.
- Intraday Yahoo 15m data for `003230`, `006260`, `052710` had a gap; strict aggregation discarded the entire day with `MALFORMED_INTERVAL`.

## Fix and controls
- `src/analysis/bollinger_atr_strategy.py` accepts an **opt-in** `allow_sparse_completed` flag, default `False` for existing strict risk analysis.
- The live 45m VWAP gate alone passes `allow_sparse_completed=True`. An incomplete 45m bin is discarded; no invented prices/volumes.
- **Still WAIT** when the latest completed bin is missing, fewer than 26 completed bars remain, or OHLCV is invalid.
- Patch script: `scripts/patch_live_45m_vwap_20261016.py`; exact-match assertion and test against source image.
- 6 synthetic safety tests passed locally and in the actual built image. Live spot checks: `003230`, `006260`, `052710` each changed from `MALFORMED_INTERVAL` to `VALID` (37 completed bars).

## Runtime / rollback
- Current image: `asia-northeast3-docker.pkg.dev/stock-monitoring-prod/stockbot-repo/youtube-signal-service:sparse-vwap45-20261016`.
- Serving revision after explicit traffic update: `youtube-signal-service-00027-mdz` (100%).
- Previous revision: `youtube-signal-service-00030-sig` (left in place for rollback).
- Rollback command: `gcloud run services update-traffic youtube-signal-service --project=stock-monitoring-prod --region=asia-northeast3 --to-revisions=youtube-signal-service-00030-sig=100`.
- Triggers, 30-day TTL, existing buy thresholds, Jev gate, email and trading rules unchanged.
- Observe future candidate review rows for `45M_VWAP_UNAVAILABLE`; some will still be legitimate missing-data WAIT cases.
