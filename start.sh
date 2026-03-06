#!/bin/bash
# ============================================================================
# Sakhi Backend — Startup Script
# Runs FastAPI (token server) + LiveKit Voice Agent in the same container
#
# Strategy:
#   1. Start the Voice Agent FIRST (in background) so it can begin registering
#      with LiveKit Cloud while we wait.
#   2. Give it time to fully initialize before accepting token requests.
#   3. Start FastAPI LAST so tokens are only issued after the agent is ready.
# ============================================================================

set -e

echo "Starting Sakhi Backend..."

# Start the LiveKit Voice Agent in the background FIRST
# It needs ~10-30s to download models, initialize, and register with LiveKit
echo "Starting LiveKit Voice Agent..."
python agent.py start &
AGENT_PID=$!

# Wait for the agent worker to register with LiveKit Cloud
# FastAPI must NOT start before this, or token dispatches will be dropped
echo "Waiting for agent to initialize and register..."
sleep 30

# Now start the FastAPI token server in the background
# By this point the agent is registered and ready to accept dispatches
echo "Starting FastAPI on port 8000..."
python -m uvicorn api:app --host 0.0.0.0 --port 8000 &
FASTAPI_PID=$!

# Wait for either process to exit
wait -n $AGENT_PID $FASTAPI_PID 2>/dev/null || true

# If one exits, clean up the other
echo "A process exited, shutting down..."
kill $AGENT_PID $FASTAPI_PID 2>/dev/null || true
wait
