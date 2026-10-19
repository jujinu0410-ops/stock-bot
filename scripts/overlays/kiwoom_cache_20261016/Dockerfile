# Rebuild only on the verified production image digest. Never replace the newer
# unified service with the old services/youtube_signal_service.py from main.
FROM asia-northeast3-docker.pkg.dev/stock-monitoring-prod/stockbot-repo/youtube-signal-service@sha256:4522bc92bc9e80e58b1b61a498acc167db86903e5f80deab012763b24cac21a7
USER root
COPY --chown=stockbot:stockbot scripts/overlays/kiwoom_cache_20261016/unified_scan.py /app/services/unified_scan.py
COPY --chown=stockbot:stockbot scripts/overlays/kiwoom_cache_20261016/youtube_signal_service.py /app/services/youtube_signal_service.py
COPY --chown=stockbot:stockbot src/analysis/bollinger_atr_strategy.py /app/src/analysis/bollinger_atr_strategy.py
COPY --chown=stockbot:stockbot src/analysis/intraday_analysis.py /app/src/analysis/intraday_analysis.py
COPY scripts/overlays/kiwoom_cache_20261016/test_cache_opt.py /opt/test_cache_opt.py
COPY tests/test_sparse_completed_45m.py /opt/test_sparse_completed_45m.py
RUN python -m py_compile /app/services/unified_scan.py /app/services/youtube_signal_service.py /app/src/analysis/bollinger_atr_strategy.py /app/src/analysis/intraday_analysis.py
USER stockbot
