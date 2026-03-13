#!/bin/bash
# ============================================================================
# Sakhi Backend — Startup Script
# Runs FastAPI (token server) + LiveKit Voice Agent + Emotion Detector + Story Agent
#
# Strategy:
#   1. Start the Voice Agent FIRST (in background) so it can begin registering
#      with LiveKit Cloud while we wait.
#   2. Start the Emotion Detector (in background) — separate AgentServer.
#   3. Start the Story Agent (in background) — separate AgentServer.
#   4. Give them time to fully initialize before accepting token requests.
#   5. Start FastAPI LAST so tokens are only issued after agents are ready.
# ============================================================================

set -e

echo "Starting Sakhi Backend..."

# Start the LiveKit Voice Agent in the background FIRST
echo "Starting LiveKit Voice Agent..."
python agent.py start &
AGENT_PID=$!

# Start the Emotion Detector in the background
echo "Starting Emotion Detector..."
python emotion_detector.py start &
EMOTION_PID=$!

# Start the Story Agent in the background
echo "Starting Story Agent..."
python story_entrypoint.py start &
STORY_PID=$!

# Wait for the agent workers to register with LiveKit Cloud
echo "Waiting for agents to initialize and register..."
sleep 30

# Now start the FastAPI token server in the background
# By this point the agents are registered and ready to accept dispatches
echo "Starting FastAPI on port 8000 using run.py..."
python run.py &
FASTAPI_PID=$!

# Wait for any process to exit
wait -n $AGENT_PID $EMOTION_PID $STORY_PID $FASTAPI_PID 2>/dev/null || true

# If one exits, clean up the others
echo "A process exited, shutting down..."
kill $AGENT_PID $EMOTION_PID $STORY_PID $FASTAPI_PID 2>/dev/null || true
wait
