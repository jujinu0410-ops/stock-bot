# Rebuild only on the verified production image digest. Never replace the newer
# unified service with the old services/youtube_signal_service.py from main.
FROM asia-northeast3-docker.pkg.dev/stock-monitoring-prod/stockbot-repo/youtube-signal-service@sha256:67bd7aaf97ca1669dbeae709cbd701db8a7827e8ffb16e41bf6df5d60645dd42
USER root
COPY --chown=stockbot:stockbot scripts/overlays/kiwoom_cache_20261016/unified_scan.py /app/services/unified_scan.py
COPY scripts/overlays/kiwoom_cache_20261016/test_cache_opt.py /opt/test_cache_opt.py
RUN python -m py_compile /app/services/unified_scan.py /app/services/youtube_signal_service.py
USER stockbot
