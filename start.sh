#!/bin/bash
# ============================================================================
# Sakhi Backend — Startup Script
# Runs FastAPI (token server) + LiveKit Agent (voice + emotion detector)
#

# Strategy:
#   1. Start the Agent FIRST (in background) — a single AgentServer that
#      handles both "sakhi-agent" and "emotion-detector" dispatches.
#   2. Give it time to fully initialize and register with LiveKit Cloud.
#   3. Start FastAPI LAST so tokens are only issued after agents are ready.
# ============================================================================

set -e

echo "Starting Sakhi Backend..."

# Start the LiveKit Agent in the background FIRST
# One AgentServer handles both voice agent + emotion detector
echo "Starting LiveKit Agent (voice + emotion)..."
python agent.py start &
AGENT_PID=$!

# Wait for the agent worker to register with LiveKit Cloud
# FastAPI must NOT start before this, or token dispatches will be dropped
echo "Waiting for agent to initialize and register..."
sleep 30

# Now start the FastAPI token server in the background
echo "Starting FastAPI on port 8000 using run.py..."
python run.py &
FASTAPI_PID=$!

# Wait for any process to exit
wait -n $AGENT_PID $FASTAPI_PID 2>/dev/null || true

# If one exits, clean up the other
echo "A process exited, shutting down..."
kill $AGENT_PID $FASTAPI_PID 2>/dev/null || true
wait
