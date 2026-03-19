"""
Sakhi Backend — Account Service
==================================
Signup, login, refresh, logout business logic.
"""

import logging

import bcrypt

from db.pool import get_pool
from services.jwt_service import (
    create_account_token,
    create_refresh_token,
)

logger = logging.getLogger("sakhi.auth")


def _hash_password(password: str) -> str:
    """Hash a password with bcrypt."""
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def _verify_password(password: str, hashed: str) -> bool:
    """Verify a password against a bcrypt hash."""
    return bcrypt.checkpw(password.encode(), hashed.encode())


async def signup(email: str, password: str, family_name: str) -> dict:
    """Create a new family account with an auto-created parent profile.

    Returns: {account, parent_profile, account_token, refresh_token}
    """
    pool = get_pool()
    password_hash = _hash_password(password)

    async with pool.acquire() as conn:
        # Check if email already exists
        existing = await conn.fetchval("SELECT id FROM accounts WHERE email = $1", email)
        if existing:
            raise ValueError("An account with this email already exists")

        # Create the account
        account = await conn.fetchrow(
            """
            INSERT INTO accounts (email, password_hash, family_name)
            VALUES ($1, $2, $3)
            RETURNING id, email, family_name, plan, created_at
            """,
            email,
            password_hash,
            family_name,
        )
        account_id = str(account["id"])

        # Auto-create the parent profile
        parent_profile = await conn.fetchrow(
            """
            INSERT INTO profiles (account_id, type, display_name)
            VALUES ($1, 'parent', $2)
            RETURNING id, account_id, type, display_name, avatar, age, created_at
            """,
            account["id"],
            family_name,
        )

        # Create tokens
        account_token, account_jti, account_exp = create_account_token(account_id)
        refresh_token, refresh_jti, refresh_exp = create_refresh_token(account_id)

        # Record sessions
        await conn.execute(
            """
            INSERT INTO sessions (account_id, token_type, token_jti, expires_at)
            VALUES ($1, 'account', $2, $3), ($1, 'refresh', $4, $5)
            """,
            account["id"],
            account_jti,
            account_exp,
            refresh_jti,
            refresh_exp,
        )

    logger.info(f"Account created: {email} (id={account_id})")

    return {
        "account": _record_to_dict(account),
        "parent_profile": _record_to_dict(parent_profile),
        "account_token": account_token,
        "refresh_token": refresh_token,
    }


async def login(email: str, password: str) -> dict:
    """Verify credentials and return tokens + all profiles.

    Returns: {account, profiles, account_token, refresh_token}
    """
    pool = get_pool()

    async with pool.acquire() as conn:
        account = await conn.fetchrow("SELECT * FROM accounts WHERE email = $1", email)
        if not account:
            raise ValueError("Invalid email or password")

        if not _verify_password(password, account["password_hash"]):
            raise ValueError("Invalid email or password")

        account_id = str(account["id"])

        # Fetch all profiles for the picker screen
        profiles = await conn.fetch(
            "SELECT id, account_id, type, display_name, avatar, age, created_at FROM profiles WHERE account_id = $1 ORDER BY type DESC, created_at",
            account["id"],
        )

        # Create tokens
        account_token, account_jti, account_exp = create_account_token(account_id)
        refresh_token, refresh_jti, refresh_exp = create_refresh_token(account_id)

        # Record sessions
        await conn.execute(
            """
            INSERT INTO sessions (account_id, token_type, token_jti, expires_at)
            VALUES ($1, 'account', $2, $3), ($1, 'refresh', $4, $5)
            """,
            account["id"],
            account_jti,
            account_exp,
            refresh_jti,
            refresh_exp,
        )

    logger.info(f"Login successful: {email}")

    return {
        "account": {
            "id": account_id,
            "email": account["email"],
            "family_name": account["family_name"],
            "plan": account["plan"],
        },
        "profiles": [_record_to_dict(p) for p in profiles],
        "account_token": account_token,
        "refresh_token": refresh_token,
    }


async def refresh(refresh_jti: str, account_id: str) -> dict:
    """Issue a new account token using a valid refresh token.

    Returns: {account_token}
    """
    pool = get_pool()

    async with pool.acquire() as conn:
        # Verify the refresh session exists and is not revoked
        session = await conn.fetchrow(
            """
            SELECT id FROM sessions
            WHERE token_jti = $1 AND account_id = $2
              AND token_type = 'refresh' AND revoked = false
              AND expires_at > now()
            """,
            refresh_jti,
            account_id,
        )
        if not session:
            raise ValueError("Invalid or expired refresh token")

        # Issue a new account token
        account_token, account_jti, account_exp = create_account_token(account_id)

        # Record the new account session
        await conn.execute(
            """
            INSERT INTO sessions (account_id, token_type, token_jti, expires_at)
            VALUES ($1, 'account', $2, $3)
            """,
            account_id,
            account_jti,
            account_exp,
        )

    logger.info(f"Token refreshed for account {account_id}")
    return {"account_token": account_token}


async def logout(account_id: str) -> None:
    """Revoke all active sessions for the account."""
    pool = get_pool()

    async with pool.acquire() as conn:
        await conn.execute(
            """
            UPDATE sessions SET revoked = true
            WHERE account_id = $1 AND revoked = false
            """,
            account_id,
        )

    logger.info(f"All sessions revoked for account {account_id}")


def _record_to_dict(record) -> dict:
    """Convert an asyncpg Record to a JSON-safe dict."""
    d = dict(record)
    for key, value in d.items():
        if hasattr(value, "isoformat"):
            d[key] = value.isoformat()
        elif isinstance(value, (bytes, memoryview)):
            d[key] = str(value)
        else:
            # UUID → str
            d[key] = str(value) if not isinstance(value, (str, int, float, bool, type(None))) else value
    return d
