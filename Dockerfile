# ============================================================================
# Sakhi Backend — Production Dockerfile (FastAPI only)
# Voice agents are deployed separately on LiveKit Cloud
# ============================================================================

# -- Stage 1: Build dependencies ---------------------------------------------
FROM python:3.12-slim AS builder

WORKDIR /app

# Install build dependencies for compiled packages (numpy, onnxruntime, etc.)
RUN apt-get update && \
    apt-get install -y --no-install-recommends gcc g++ && \
    rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

# -- Stage 2: Production runtime ----------------------------------------------
FROM python:3.12-slim AS runtime

# Create non-root user for security
RUN groupadd -g 1001 sakhi && \
    useradd -u 1001 -g sakhi -m -s /bin/bash sakhi

WORKDIR /app

# Copy installed packages from builder stage
COPY --from=builder /install /usr/local

# Copy application code — FastAPI server + services
COPY run.py .
COPY api/ api/
COPY services/ services/
COPY db/ db/
COPY utils/ utils/

# Create logs directory owned by sakhi user
RUN mkdir -p /app/logs && chown sakhi:sakhi /app/logs

# Switch to non-root user
USER sakhi

# Expose the FastAPI port
EXPOSE 8000

# Health check for the FastAPI server
HEALTHCHECK --interval=30s --timeout=10s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/api/health')" || exit 1

# Start FastAPI server
CMD ["python", "run.py"]
