"""
Sakhi Backend — Auth Dependencies
====================================
FastAPI dependency functions for extracting and validating tokens
from the Authorization header.
"""

import jwt as pyjwt
from fastapi import Header, HTTPException, status

from db.pool import get_pool
from services.jwt_service import decode_token


async def _extract_and_validate(authorization: str, expected_type: str) -> dict:
    """Shared logic: decode JWT, check type, verify session not revoked."""
    if not authorization.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing or invalid Authorization header",
        )

    token = authorization[7:]  # Strip "Bearer "

    try:
        claims = decode_token(token)
    except pyjwt.ExpiredSignatureError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token has expired",
        ) from None
    except pyjwt.InvalidTokenError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token",
        ) from None

    if claims.get("type") != expected_type:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Expected {expected_type} token",
        )

    # Check session is not revoked
    pool = get_pool()
    async with pool.acquire() as conn:
        session = await conn.fetchrow(
            """
            SELECT id FROM sessions
            WHERE token_jti = $1 AND revoked = false AND expires_at > now()
            """,
            claims["jti"],
        )
    if not session:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token has been revoked or session expired",
        )

    return claims


async def require_account_token(
    authorization: str = Header(..., alias="Authorization"),
) -> dict:
    """Dependency: validates an account token. Returns decoded claims."""
    return await _extract_and_validate(authorization, "account")


async def require_profile_token(
    authorization: str = Header(..., alias="Authorization"),
) -> dict:
    """Dependency: validates a profile token. Returns decoded claims."""
    return await _extract_and_validate(authorization, "profile")


async def require_refresh_token(
    authorization: str = Header(..., alias="Authorization"),
) -> dict:
    """Dependency: validates a refresh token. Returns decoded claims."""
    return await _extract_and_validate(authorization, "refresh")
