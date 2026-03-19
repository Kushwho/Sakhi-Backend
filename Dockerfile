# ============================================================================
# Sakhi Backend — Production Dockerfile (FastAPI only)
# Voice agents are deployed separately on LiveKit Cloud
# ============================================================================

# -- Stage 1: Build dependencies ---------------------------------------------
FROM python:3.12-slim AS builder

WORKDIR /app

RUN apt-get update && \
    apt-get install -y --no-install-recommends gcc g++ libpq-dev && \
    rm -rf /var/lib/apt/lists/*

COPY requirements-api.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements-api.txt

# -- Stage 2: Production runtime ----------------------------------------------
FROM python:3.12-slim AS runtime

# Install libpq runtime + create non-root user in one layer
RUN apt-get update && \
    apt-get install -y --no-install-recommends libpq5 && \
    rm -rf /var/lib/apt/lists/* && \
    groupadd -g 1001 sakhi && \
    useradd -u 1001 -g sakhi -m -s /bin/bash sakhi

WORKDIR /app

# Copy installed packages from builder stage
COPY --from=builder /install /usr/local

# Copy application code
COPY run.py .
COPY api/ api/
COPY services/ services/
COPY db/ db/
COPY utils/ utils/

# Create logs directory owned by sakhi user
RUN mkdir -p /app/logs && chown sakhi:sakhi /app/logs

USER sakhi

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=10s --start-period=15s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/api/health')" || exit 1

CMD ["python", "run.py"]
