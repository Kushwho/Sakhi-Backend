"""
Sakhi Backend — Profile Service
==================================
Profile CRUD, enter/exit, and listing.
"""

import logging
import uuid

import bcrypt

from db.pool import get_pool
from services.jwt_service import create_profile_token

logger = logging.getLogger("sakhi.auth")


async def list_profiles(account_id: str) -> list[dict]:
    """Return all profiles for a family account (picker screen)."""
    pool = get_pool()

    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT id, account_id, type, display_name, avatar, age, created_at
            FROM profiles
            WHERE account_id = $1
            ORDER BY type DESC, created_at
            """,
            uuid.UUID(account_id),
        )

    return [_record_to_dict(r) for r in rows]


async def create_child_profile(
    account_id: str,
    display_name: str,
    age: int | None = None,
    avatar: str | None = None,
) -> dict:
    """Create a new child profile for the family."""
    pool = get_pool()

    async with pool.acquire() as conn:
        profile = await conn.fetchrow(
            """
            INSERT INTO profiles (account_id, type, display_name, avatar, age)
            VALUES ($1, 'child', $2, $3, $4)
            RETURNING id, account_id, type, display_name, avatar, age, created_at
            """,
            uuid.UUID(account_id),
            display_name,
            avatar,
            age,
        )

    logger.info(f"Child profile created: {display_name} (account={account_id})")
    return _record_to_dict(profile)


async def enter_profile(
    profile_id: str,
    account_id: str,
    password: str | None = None,
) -> dict:
    """Enter a profile. Child = instant. Parent = requires password.

    Returns: {profile, profile_token}
    """
    pool = get_pool()

    async with pool.acquire() as conn:
        # Fetch the profile
        profile = await conn.fetchrow(
            """
            SELECT id, account_id, type, display_name, avatar, age, created_at
            FROM profiles
            WHERE id = $1 AND account_id = $2
            """,
            uuid.UUID(profile_id),
            uuid.UUID(account_id),
        )
        if not profile:
            raise ValueError("Profile not found")

        # Parent profiles require password verification
        if profile["type"] == "parent":
            if not password:
                raise ValueError("Password is required to enter the parent profile")

            account = await conn.fetchrow(
                "SELECT password_hash FROM accounts WHERE id = $1",
                uuid.UUID(account_id),
            )
            if not bcrypt.checkpw(password.encode(), account["password_hash"].encode()):
                raise ValueError("Incorrect password")

        # Issue a profile token
        profile_token, jti, expires_at = create_profile_token(
            account_id, profile_id, profile["type"]
        )

        # Record the profile session
        await conn.execute(
            """
            INSERT INTO sessions (account_id, profile_id, token_type, token_jti, expires_at)
            VALUES ($1, $2, 'profile', $3, $4)
            """,
            uuid.UUID(account_id),
            uuid.UUID(profile_id),
            jti,
            expires_at,
        )

    logger.info(f"Profile entered: {profile['display_name']} ({profile['type']})")

    return {
        "profile": _record_to_dict(profile),
        "profile_token": profile_token,
    }


async def exit_profile(token_jti: str) -> None:
    """Revoke a profile token session (back to picker)."""
    pool = get_pool()

    async with pool.acquire() as conn:
        result = await conn.execute(
            """
            UPDATE sessions SET revoked = true
            WHERE token_jti = $1 AND token_type = 'profile' AND revoked = false
            """,
            token_jti,
        )

    logger.info(f"Profile session revoked: {token_jti}")


async def get_current_profile(profile_id: str) -> dict:
    """Fetch the full profile by ID (used with profile token)."""
    pool = get_pool()

    async with pool.acquire() as conn:
        profile = await conn.fetchrow(
            """
            SELECT id, account_id, type, display_name, avatar, age, created_at
            FROM profiles
            WHERE id = $1
            """,
            uuid.UUID(profile_id),
        )

    if not profile:
        raise ValueError("Profile not found")

    return _record_to_dict(profile)


def _record_to_dict(record) -> dict:
    """Convert an asyncpg Record to a JSON-safe dict."""
    d = dict(record)
    for key, value in d.items():
        if hasattr(value, "isoformat"):
            d[key] = value.isoformat()
        elif isinstance(value, (bytes, memoryview)):
            d[key] = str(value)
        else:
            d[key] = str(value) if not isinstance(value, (str, int, float, bool, type(None))) else value
    return d
