"""
Sakhi Backend — Dashboard API Routes
========================================
Parent-facing endpoints for the 5 dashboard metrics.
Requires a parent profile token.
"""

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from api.dependencies import require_profile_token
from services.dashboard import (
    get_alerts,
    get_mood_summary,
    get_overview,
    get_streak,
    get_time_spent,
    get_topics_explored,
)

logger = logging.getLogger("sakhi.api.dashboard")

router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])


# ---------------------------------------------------------------------------
# Auth helper: ensure parent token, resolve profile_id
# ---------------------------------------------------------------------------


def _resolve_profile_id(claims: dict, profile_id: str | None) -> str:
    """Resolve which child profile to fetch metrics for.

    A parent can view any child profile in their account.
    If profile_id is not provided, return the parent's own profile (unlikely
    to have session data, but still valid).
    """
    if claims.get("profile_type") != "parent":
        raise HTTPException(
            status_code=403,
            detail="Only parent profiles can access the dashboard",
        )
    # TODO: Verify the requested profile_id belongs to the same account
    return profile_id or claims["profile_id"]


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("/time-spent")
async def dashboard_time_spent(
    profile_id: str = Query(..., description="Child profile ID"),
    days: int = Query(7, ge=1, le=90),
    claims: dict = Depends(require_profile_token),
):
    """Time spent with Sakhi — total minutes and daily breakdown."""
    pid = _resolve_profile_id(claims, profile_id)
    return await get_time_spent(pid, days)


@router.get("/mood")
async def dashboard_mood(
    profile_id: str = Query(..., description="Child profile ID"),
    days: int = Query(7, ge=1, le=90),
    claims: dict = Depends(require_profile_token),
):
    """Mood summary — session moods and emotion distribution."""
    pid = _resolve_profile_id(claims, profile_id)
    return await get_mood_summary(pid, days)


@router.get("/topics")
async def dashboard_topics(
    profile_id: str = Query(..., description="Child profile ID"),
    days: int = Query(7, ge=1, le=90),
    claims: dict = Depends(require_profile_token),
):
    """Topics explored — top 5 deduplicated topics."""
    pid = _resolve_profile_id(claims, profile_id)
    return await get_topics_explored(pid, days)


@router.get("/streak")
async def dashboard_streak(
    profile_id: str = Query(..., description="Child profile ID"),
    claims: dict = Depends(require_profile_token),
):
    """Streak — current and longest consecutive days."""
    pid = _resolve_profile_id(claims, profile_id)
    return await get_streak(pid)


@router.get("/alerts")
async def dashboard_alerts(
    profile_id: str = Query(..., description="Child profile ID"),
    limit: int = Query(20, ge=1, le=100),
    claims: dict = Depends(require_profile_token),
):
    """Sakhi Noticed — alert feed for parents."""
    pid = _resolve_profile_id(claims, profile_id)
    return await get_alerts(pid, limit)


@router.get("/overview")
async def dashboard_overview(
    profile_id: str = Query(..., description="Child profile ID"),
    claims: dict = Depends(require_profile_token),
):
    """All 5 metrics in one call — for the main dashboard view."""
    pid = _resolve_profile_id(claims, profile_id)
    return await get_overview(pid)
