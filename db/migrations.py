"""
Sakhi Backend — Database Migrations
======================================
Creates tables on startup if they don't exist.
"""

import logging

import asyncpg

logger = logging.getLogger("sakhi.db")

MIGRATIONS = [
    # ------- accounts -------
    """
    CREATE TABLE IF NOT EXISTS accounts (
        id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        email       TEXT UNIQUE NOT NULL,
        password_hash TEXT NOT NULL,
        family_name TEXT NOT NULL,
        plan        TEXT NOT NULL DEFAULT 'free',
        created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
    );
    """,
    # ------- profiles -------
    """
    CREATE TABLE IF NOT EXISTS profiles (
        id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        account_id  UUID NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
        type        TEXT NOT NULL CHECK (type IN ('parent', 'child')),
        display_name TEXT NOT NULL,
        avatar      TEXT,
        age         INT,
        created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
    );
    """,
    # ------- sessions -------
    """
    CREATE TABLE IF NOT EXISTS sessions (
        id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        account_id  UUID NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
        profile_id  UUID REFERENCES profiles(id) ON DELETE CASCADE,
        token_type  TEXT NOT NULL CHECK (token_type IN ('account', 'refresh', 'profile')),
        token_jti   TEXT UNIQUE NOT NULL,
        expires_at  TIMESTAMPTZ NOT NULL,
        revoked     BOOLEAN NOT NULL DEFAULT false,
        created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
    );
    """,
    # ------- indexes -------
    """
    CREATE INDEX IF NOT EXISTS idx_profiles_account_id ON profiles(account_id);
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_sessions_token_jti ON sessions(token_jti);
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_sessions_account_id ON sessions(account_id);
    """,
    # ------- emotion_snapshots -------
    """
    CREATE TABLE IF NOT EXISTS emotion_snapshots (
        id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        profile_id  UUID NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
        session_id  UUID,
        room_name   TEXT NOT NULL,
        emotion     TEXT NOT NULL,
        score       REAL NOT NULL,
        top_3       JSONB,
        recorded_at TIMESTAMPTZ NOT NULL DEFAULT now()
    );
    """,
    # ------- session_summaries -------
    """
    CREATE TABLE IF NOT EXISTS session_summaries (
        id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        profile_id    UUID NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
        room_name     TEXT NOT NULL,
        started_at    TIMESTAMPTZ NOT NULL,
        ended_at      TIMESTAMPTZ NOT NULL,
        duration_secs INT NOT NULL,
        mood_summary  TEXT,
        topics        JSONB DEFAULT '[]',
        turn_count    INT DEFAULT 0,
        transcript    JSONB,
        created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
    );
    """,
    # ------- alerts -------
    """
    CREATE TABLE IF NOT EXISTS alerts (
        id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        profile_id  UUID NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
        session_id  UUID REFERENCES session_summaries(id),
        alert_type  TEXT NOT NULL CHECK (alert_type IN ('emotion', 'content', 'pattern')),
        severity    TEXT NOT NULL CHECK (severity IN ('info', 'warning', 'critical')),
        title       TEXT NOT NULL,
        description TEXT,
        recorded_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        dismissed   BOOLEAN DEFAULT false
    );
    """,
    # ------- dashboard indexes -------
    """
    CREATE INDEX IF NOT EXISTS idx_emotion_snapshots_profile
        ON emotion_snapshots(profile_id, recorded_at DESC);
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_session_summaries_profile
        ON session_summaries(profile_id, ended_at DESC);
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_alerts_profile
        ON alerts(profile_id, recorded_at DESC);
    """,
    # ------- pgvector extension -------
    """
    CREATE EXTENSION IF NOT EXISTS vector;
    """,
    # ------- memories (long-term memory store) -------
    """
    CREATE TABLE IF NOT EXISTS memories (
        id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        profile_id  UUID NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
        service     TEXT NOT NULL DEFAULT 'sakhi',
        content     TEXT NOT NULL,
        embedding   vector(384),
        metadata    JSONB DEFAULT '{}',
        strength    REAL NOT NULL DEFAULT 1.0,
        created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
        updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
    );
    """,
    # ------- memory indexes -------
    """
    CREATE INDEX IF NOT EXISTS idx_memories_namespace
        ON memories(service, profile_id);
    """,
]


async def run_migrations(pool: asyncpg.Pool) -> None:
    """Execute all migrations in order."""
    async with pool.acquire() as conn:
        for i, sql in enumerate(MIGRATIONS):
            await conn.execute(sql)
        logger.info(f"Database migrations completed ({len(MIGRATIONS)} statements)")
