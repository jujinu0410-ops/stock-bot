# Exact running image: only the four mail presentation files are replaced.
ARG STOCKBOT_BASE_IMAGE=asia-northeast3-docker.pkg.dev/stock-monitoring-prod/stockbot-repo/stockbot-cloud-runner@sha256:9378eb6c66bd6e002df939bd13240203519fcc5b53808aa9cd7d0bd15bfb6b34
FROM ${STOCKBOT_BASE_IMAGE} AS runtime
COPY --chown=stockbot:stockbot main.py /app/main.py
COPY --chown=stockbot:stockbot src/notifications/gmail_notifier.py src/notifications/intraday_data.py src/notifications/intraday_renderer.py /app/src/notifications/

FROM runtime AS test
ENV STOCKBOT_TEST_MODE=1 STOCKBOT_CLOUD_MODE=1 KIWOOM_USE_MOCK=True
# The offline test container has no metadata service or live credentials.
ENV GCE_METADATA_TIMEOUT=1 GCE_METADATA_DETECT_RETRIES=1
COPY --chown=stockbot:stockbot tests/ /app/tests/
COPY --chown=stockbot:stockbot scripts/run_tests_isolated.py /app/scripts/run_tests_isolated.py
ENTRYPOINT ["python", "scripts/run_tests_isolated.py"]
