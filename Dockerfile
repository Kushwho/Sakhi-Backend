# ============================================================================
# Sakhi Voice Agent — Production Dockerfile
# Runs both the FastAPI token server and the LiveKit Voice Agent
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

# Copy application code
COPY agent.py .
COPY api.py .
COPY start.sh .

# Make start script executable
RUN chmod +x start.sh

# Switch to non-root user FIRST so models are saved in its home directory
USER sakhi

# Download Silero VAD + Turn Detector models at build time (not at runtime)
RUN python agent.py download-files

# Expose the FastAPI port
EXPOSE 8000

# Health check for the FastAPI server
HEALTHCHECK --interval=30s --timeout=10s --start-period=15s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/api/health')" || exit 1

# Start both services
CMD ["bash", "start.sh"]
