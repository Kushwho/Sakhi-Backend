#!/bin/bash
# ============================================================================
# Sakhi Backend — Startup Script
# Runs FastAPI (token server) + LiveKit Voice Agent in the same container
# ============================================================================

set -e

echo "Starting Sakhi Backend..."

# Start the FastAPI token server in the background
echo "Starting FastAPI on port 8000..."
python -m uvicorn api:app --host 0.0.0.0 --port 8000 &
FASTAPI_PID=$!

# Start the LiveKit Voice Agent in the foreground
echo "Starting LiveKit Voice Agent..."
python agent.py start

# If the agent exits, also stop FastAPI
kill $FASTAPI_PID 2>/dev/null
