"""
Sakhi Backend — FastAPI Server
================================
Entrypoint: ``uvicorn api.routes:app --reload --port 8000``

Sets up the FastAPI application with CORS and routes.
API endpoint logic lives in api/routes.py.
"""

from api.routes import app  # noqa: F401

# The app is configured and ready to serve from api/routes.py.
# Run with: 

#
# In production (Docker), start.sh runs:
#   python -m uvicorn api.routes:app --host 0.0.0.0 --port 8000
