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
from db.pool import get_pool
from services import accounts, profiles
from services import msg91 as msg91_service

logger = logging.getLogger("sakhi.api.auth")

router = APIRouter(prefix="/auth", tags=["auth"])


# ---------------------------------------------------------------------------
# Request / Response models
# ---------------------------------------------------------------------------


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class SendOtpRequest(BaseModel):
    email: EmailStr


class VerifyOtpAndSignupRequest(BaseModel):
    email: EmailStr
    password: str
    family_name: str
    otp: str


class ResendOtpRequest(BaseModel):
    email: EmailStr


class GoogleAuthRequest(BaseModel):
    id_token: str
    family_name: str
    password: str


class ForgotPasswordRequest(BaseModel):
    email: EmailStr


class ResetPasswordRequest(BaseModel):
    email: EmailStr
    otp: str
    new_password: str


class CreateChildRequest(BaseModel):
    display_name: str
    age: int | None = None
    avatar: str | None = None


class EnterProfileRequest(BaseModel):
    password: str | None = None


# ---------------------------------------------------------------------------
# Public endpoints (no auth required)
# ---------------------------------------------------------------------------


@router.post("/send-otp")
async def send_otp(req: SendOtpRequest):
    """Send a 6-digit OTP to the given email via MSG91.

    Checks if the email is already registered before sending.
    Returns { request_id, message } for the frontend to track verification.
    """
    pool = get_pool()
    async with pool.acquire() as conn:
        existing = await conn.fetchval("SELECT id FROM accounts WHERE email = $1", req.email)
        if existing:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="An account with this email already exists",
            ) from None

    try:
        await msg91_service.send_otp(req.email)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(e)) from None

    return {"message": "OTP sent to your email"}


@router.post("/verify-otp-signup", status_code=status.HTTP_201_CREATED)
async def verify_otp_and_signup(req: VerifyOtpAndSignupRequest):
    """Verify OTP and create a new family account in one step.

    If the OTP is valid, creates the account with email_verified=True
    and returns tokens + parent profile.
    """
    try:
        await msg91_service.verify_otp(req.email, req.otp)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)) from None

    try:
        result = await accounts.signup(req.email, req.password, req.family_name, email_verified=True)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e)) from None

    return result


@router.post("/resend-otp")
async def resend_otp(req: ResendOtpRequest):
    """Resend OTP to the given email. Returns a new request_id."""
    try:
        await msg91_service.send_otp(req.email)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(e)) from None

    return {"message": "OTP resent to your email"}


@router.post("/forgot-password")
async def forgot_password(req: ForgotPasswordRequest):
    """Send an OTP to the user's email for password reset."""
    pool = get_pool()
    async with pool.acquire() as conn:
        account = await conn.fetchrow("SELECT id, auth_provider FROM accounts WHERE email = $1", req.email)
        if not account:
            # Don't reveal whether the email exists — return success either way
            return {"message": "If an account with this email exists, an OTP has been sent"}
        if account["auth_provider"] == "google":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="This account uses Google sign-in. Password reset is not applicable.",
            )

    try:
        await msg91_service.send_otp(req.email)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(e)) from None

    return {"message": "If an account with this email exists, an OTP has been sent"}


@router.post("/reset-password")
async def reset_password(req: ResetPasswordRequest):
    """Verify OTP and reset the account password."""
    try:
        await msg91_service.verify_otp(req.email, req.otp)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)) from None

    try:
        await accounts.reset_password(req.email, req.new_password)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)) from None

    return {"message": "Password has been reset successfully"}


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


@router.post("/google")
async def google_auth(req: GoogleAuthRequest):
    """Exchange Google ID token for Sakhi JWT tokens.

    Creates or authenticates a Google account.
    Google and email accounts are separate even if email matches.
    """
    try:
        result = await accounts.google_auth(req.id_token, req.family_name, req.password)
    except ValueError as e:
        error_msg = str(e)
        if "token" in error_msg.lower():
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=error_msg,
            ) from None
        if "exists" in error_msg.lower():
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=error_msg,
            ) from None
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=error_msg,
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
