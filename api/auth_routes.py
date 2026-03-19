"""
Sakhi Backend — Auth API Routes
==================================
All /auth/* endpoints for the Netflix-style authentication system.
"""

import logging

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, EmailStr

from api.dependencies import (
    require_account_token,
    require_profile_token,
    require_refresh_token,
)
from services import accounts, profiles

logger = logging.getLogger("sakhi.api.auth")

router = APIRouter(prefix="/auth", tags=["auth"])


# ---------------------------------------------------------------------------
# Request / Response models
# ---------------------------------------------------------------------------


class SignupRequest(BaseModel):
    email: EmailStr
    password: str
    family_name: str


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class CreateChildRequest(BaseModel):
    display_name: str
    age: int | None = None
    avatar: str | None = None


class EnterProfileRequest(BaseModel):
    password: str | None = None


# ---------------------------------------------------------------------------
# Public endpoints (no auth required)
# ---------------------------------------------------------------------------


@router.post("/signup", status_code=status.HTTP_201_CREATED)
async def signup(req: SignupRequest):
    """Create a new family account with auto-created parent profile."""
    try:
        result = await accounts.signup(req.email, req.password, req.family_name)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e)) from None

    return result


@router.post("/login")
async def login(req: LoginRequest):
    """Verify credentials, return tokens + all profiles for picker screen."""
    try:
        result = await accounts.login(req.email, req.password)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password",
        ) from None

    return result


# ---------------------------------------------------------------------------
# Refresh endpoint (requires refresh token)
# ---------------------------------------------------------------------------


@router.post("/refresh")
async def refresh(claims: dict = Depends(require_refresh_token)):
    """Silently swap an expired account token for a new one."""
    try:
        result = await accounts.refresh(claims["jti"], claims["sub"])
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(e)) from None

    return result


# ---------------------------------------------------------------------------
# Account-token-protected endpoints
# ---------------------------------------------------------------------------


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(claims: dict = Depends(require_account_token)):
    """Revoke all sessions for this account."""
    await accounts.logout(claims["sub"])


@router.get("/profiles")
async def get_profiles(claims: dict = Depends(require_account_token)):
    """Return all profiles for the picker screen."""
    return await profiles.list_profiles(claims["sub"])


@router.post("/profiles", status_code=status.HTTP_201_CREATED)
async def create_child(
    req: CreateChildRequest,
    claims: dict = Depends(require_account_token),
):
    """Create a new child profile."""
    return await profiles.create_child_profile(
        account_id=claims["sub"],
        display_name=req.display_name,
        age=req.age,
        avatar=req.avatar,
    )


@router.post("/profiles/{profile_id}/enter")
async def enter_profile(
    profile_id: str,
    req: EnterProfileRequest,
    claims: dict = Depends(require_account_token),
):
    """Enter a profile. Child = instant token. Parent = password required."""
    try:
        result = await profiles.enter_profile(
            profile_id=profile_id,
            account_id=claims["sub"],
            password=req.password,
        )
    except ValueError as e:
        error_msg = str(e)
        if "password" in error_msg.lower():
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=error_msg) from None
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=error_msg) from None

    return result


# ---------------------------------------------------------------------------
# Profile-token-protected endpoints
# ---------------------------------------------------------------------------


@router.post("/profiles/exit", status_code=status.HTTP_204_NO_CONTENT)
async def exit_profile(claims: dict = Depends(require_profile_token)):
    """Exit current profile, revoke profile token, back to picker."""
    await profiles.exit_profile(claims["jti"])


@router.get("/profiles/me")
async def get_me(claims: dict = Depends(require_profile_token)):
    """Return the currently active profile."""
    return await profiles.get_current_profile(claims["profile_id"])
