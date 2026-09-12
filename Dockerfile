# =============================================================================
# Stage 1: Base Environment (Python 3.12 + System Dependencies + Pip Packages)
# =============================================================================
FROM python:3.12-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    TZ=Asia/Seoul

WORKDIR /app

# Install system dependencies (tzdata for accurate KST, ca-certificates for HTTPS, tk/tcl for UI/scanner modules)
RUN apt-get update && apt-get install -y --no-install-recommends \
    tzdata \
    ca-certificates \
    tk \
    tcl \
    && ln -snf /usr/share/zoneinfo/$TZ /etc/localtime && echo $TZ > /etc/timezone \
    && rm -rf /var/lib/apt/lists/*

# Install python requirements
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# =============================================================================
# Stage 2: Test & Verification (Full codebase + Tests for CI/CD)
# =============================================================================
FROM base AS test

ENV STOCKBOT_TEST_MODE=1 \
    STOCKBOT_CLOUD_MODE=1

# Copy entire application and test suite
COPY . .

# Default command for test container invocation
CMD ["python", "scripts/run_tests_isolated.py"]

# =============================================================================
# Stage 3: Production Runtime (Lean runtime code ONLY, tests excluded)
# =============================================================================
FROM base AS runtime

ENV STOCKBOT_CLOUD_MODE=1 \
    STOCKBOT_STATE_DIR=/tmp/stockbot

# Create non-root runtime user
RUN useradd -m -u 1000 stockbot && \
    mkdir -p /tmp/stockbot && \
    chown -R stockbot:stockbot /tmp/stockbot /app

# Copy application runtime files ONLY (tests/ and scratch/ excluded)
COPY main.py cloud_runner.py scan_stock_for_gems.py run_gems_scanner.py ./
COPY config/ ./config/
COPY src/ ./src/
COPY scripts/ ./scripts/

# Ensure non-root permissions
RUN chown -R stockbot:stockbot /app

USER stockbot

ENTRYPOINT ["python", "cloud_runner.py"]
