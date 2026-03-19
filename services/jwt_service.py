"""
Sakhi Backend — JWT Service
==============================
Pure functions for creating and decoding JWTs.
Three token types: account (30d), refresh (90d), profile (8h).
"""

import os
import uuid
from datetime import UTC, datetime, timedelta

import jwt

# Token lifetimes
ACCOUNT_TOKEN_DAYS = 30
REFRESH_TOKEN_DAYS = 90
PROFILE_TOKEN_HOURS = 8

ALGORITHM = "HS256"


def _get_secret() -> str:
    secret = os.getenv("JWT_SECRET")
    if not secret:
        raise RuntimeError("JWT_SECRET environment variable is not set")
    return secret


def create_account_token(account_id: str) -> tuple[str, str, datetime]:
    """Create an account token (30-day).

    Returns: (jwt_string, jti, expires_at)
    """
    jti = str(uuid.uuid4())
    expires_at = datetime.now(UTC) + timedelta(days=ACCOUNT_TOKEN_DAYS)
    payload = {
        "sub": account_id,
        "jti": jti,
        "type": "account",
        "exp": expires_at,
        "iat": datetime.now(UTC),
    }
    token = jwt.encode(payload, _get_secret(), algorithm=ALGORITHM)
    return token, jti, expires_at


def create_refresh_token(account_id: str) -> tuple[str, str, datetime]:
    """Create a refresh token (90-day).

    Returns: (jwt_string, jti, expires_at)
    """
    jti = str(uuid.uuid4())
    expires_at = datetime.now(UTC) + timedelta(days=REFRESH_TOKEN_DAYS)
    payload = {
        "sub": account_id,
        "jti": jti,
        "type": "refresh",
        "exp": expires_at,
        "iat": datetime.now(UTC),
    }
    token = jwt.encode(payload, _get_secret(), algorithm=ALGORITHM)
    return token, jti, expires_at


def create_profile_token(account_id: str, profile_id: str, profile_type: str) -> tuple[str, str, datetime]:
    """Create a profile token (8-hour).

    Carries account_id, profile_id, and profile_type in claims.
    Returns: (jwt_string, jti, expires_at)
    """
    jti = str(uuid.uuid4())
    expires_at = datetime.now(UTC) + timedelta(hours=PROFILE_TOKEN_HOURS)
    payload = {
        "sub": account_id,
        "jti": jti,
        "type": "profile",
        "profile_id": profile_id,
        "profile_type": profile_type,
        "exp": expires_at,
        "iat": datetime.now(UTC),
    }
    token = jwt.encode(payload, _get_secret(), algorithm=ALGORITHM)
    return token, jti, expires_at


def decode_token(token: str) -> dict:
    """Decode and validate a JWT. Raises jwt.InvalidTokenError on failure."""
    return jwt.decode(token, _get_secret(), algorithms=[ALGORITHM])
