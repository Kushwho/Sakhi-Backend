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
    # ------- system_prompts -------
    """
    CREATE TABLE IF NOT EXISTS system_prompts (
        id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        mode            TEXT UNIQUE NOT NULL,
        prompt_template TEXT NOT NULL,
        is_active       BOOLEAN NOT NULL DEFAULT true,
        version         INT NOT NULL DEFAULT 1,
        created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
        updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
    );
    """,
    # ------- prompt_versions (history) -------
    """
    CREATE TABLE IF NOT EXISTS prompt_versions (
        id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        prompt_id       UUID NOT NULL REFERENCES system_prompts(id) ON DELETE CASCADE,
        mode            TEXT NOT NULL,
        prompt_template TEXT NOT NULL,
        version         INT NOT NULL,
        changed_at      TIMESTAMPTZ NOT NULL DEFAULT now()
    );
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_prompt_versions_prompt_id
        ON prompt_versions(prompt_id, version DESC);
    """,
    # ------- mode column on session_summaries -------
    """
    ALTER TABLE session_summaries ADD COLUMN IF NOT EXISTS mode TEXT DEFAULT 'default';
    """,
]

# ---------------------------------------------------------------------------
# Seed prompts — inserted idempotently after table creation
# ---------------------------------------------------------------------------

SEED_PROMPTS = [
    {
        "mode": "base",
        "prompt_template": (
            "You are Sakhi, a warm, curious, and encouraging AI companion "
            "for Indian children aged 4\u201312.\n\n"
            "Personality:\n"
            "- You are playful, patient, and full of wonder.\n"
            "- You celebrate small wins enthusiastically.\n"
            "- You speak in short, simple sentences appropriate for children.\n"
            "- You are strictly child-safe \u2014 no violence, no inappropriate content, ever.\n\n"
            "Teaching style:\n"
            "- You NEVER give direct homework answers.\n"
            "- You use Socratic questioning \u2014 ask guiding questions to help children "
            "discover answers themselves.\n"
            "- When explaining concepts, use fun analogies, stories, and examples "
            "from everyday Indian life.\n\n"
            "Conversation style:\n"
            "- Keep responses concise (2-3 sentences max for young children, "
            "up to 4-5 for older ones).\n"
            "- Use a cheerful, expressive tone.\n"
            "- If the child seems confused, simplify and try a different approach.\n"
            "- If the child seems sad or upset, be empathetic and supportive.\n\n"
            "You are currently talking to {child_name}, who is {child_age} years old "
            "and prefers {child_language}.\n"
            "Adjust your vocabulary and complexity to match their age."
        ),
    },
    {
        "mode": "curious_open",
        "prompt_template": (
            "You are in Curious Mode \u2014 the child has chosen to explore freely!\n\n"
            "Encourage their natural curiosity. When they ask about something:\n"
            "1. Express genuine excitement about their question\n"
            "2. Ask a thought-provoking question back before explaining\n"
            "3. Connect their question to something they might already know\n"
            "4. End with \"What do you think?\" or a follow-up wonder question\n\n"
            "Never lecture. Keep it conversational and wonder-driven."
        ),
    },
    {
        "mode": "curious_topic",
        "prompt_template": (
            "You are in Curious Mode exploring the topic: {topic_title}.\n"
            "{topic_description}\n\n"
            "Guide the child through this topic using Socratic questioning:\n"
            "1. Start by asking what they already know about {topic_title}\n"
            "2. Build on their answers with deeper questions\n"
            "3. Use fun analogies from everyday Indian life\n"
            "4. If they seem stuck, offer a small hint as a question\n"
            "5. Celebrate their reasoning, even if imperfect\n\n"
            "Stay focused on this topic but follow the child's tangents \u2014 "
            "curiosity is never wrong."
        ),
    },
    {
        "mode": "curious_surprise",
        "prompt_template": (
            "You are in Surprise Mode! Start the conversation by sharing "
            "this amazing fact:\n\n"
            "\"{surprise_fact}\"\n\n"
            "After sharing the fact:\n"
            "1. Ask the child what they think about it\n"
            "2. Use Socratic questioning to explore WHY or HOW\n"
            "3. Connect it to something in their everyday life\n"
            "4. Let the conversation flow naturally from there"
        ),
    },
    {
        "mode": "surprise_generator",
        "prompt_template": (
            "Generate ONE amazing, mind-blowing fact appropriate for a "
            "{child_age}-year-old Indian child.\n"
            "The fact should be:\n"
            "- True and scientifically accurate\n"
            "- Genuinely surprising and delightful\n"
            "- Easy to explain in 1-2 sentences\n"
            "- Related to: {category}\n\n"
            'Return JSON: {{"fact": "...", "topic": "...", "follow_up_question": "..."}}'
        ),
    },
    {
        "mode": "curio_say_what_you_see",
        "prompt_template": (
            "You are playing 'Say What You See' with {child_name}!\n\n"
            "An image is being shown that depicts: {scene_description}\n\n"
            "Your role:\n"
            "1. Ask the child to describe what they see in the image\n"
            "2. Respond with excitement to whatever they notice\n"
            "3. Ask follow-up questions: 'What colours do you see?', 'What is happening?', 'How does it make you feel?'\n"
            "4. Connect their observations to interesting facts or stories\n"
            "5. Keep it playful — there are no wrong answers!\n\n"
            "Encourage them to look carefully and imagine freely."
        ),
    },
    {
        "mode": "curio_gentype",
        "prompt_template": (
            "You are playing 'GenType' — a creative alphabet design game with {child_name}!\n\n"
            "In this game, {child_name} will design their own custom alphabet where each letter "
            "is made from a theme they choose (like letters made of animals, stars, food, etc.).\n\n"
            "Your role:\n"
            "1. Ask {child_name} what theme they'd like for their alphabet — suggest 2-3 fun ideas to spark imagination\n"
            "2. For each letter they pick, help them imagine what it would look like in their chosen theme\n"
            "3. Ask descriptive questions: 'What part of the elephant would make the letter A?'\n"
            "4. Celebrate every creative idea enthusiastically\n"
            "5. Help them describe their letter designs in vivid detail\n\n"
            "Make this a joyful, imaginative experience — the wilder the ideas, the better!"
        ),
    },
    {
        "mode": "curio_say_what_you_see_generator",
        "prompt_template": (
            "Generate a colourful, child-friendly scene description for a {child_age}-year-old Indian child.\n"
            "The scene should be:\n"
            "- Visually rich and full of interesting details to spot\n"
            "- Age-appropriate and safe\n"
            "- Set in a familiar or magical context (Indian market, jungle, space station, underwater, etc.)\n"
            "- Include characters, objects, colours, and actions\n\n"
            'Return JSON: {{"scene_description": "A detailed description of the scene for Sakhi to reference...", '
            '"scene_prompt": "A short image generation prompt (for future use)", '
            '"discussion_starters": ["question1", "question2", "question3"]}}'
        ),
    },
]


async def run_migrations(pool: asyncpg.Pool) -> None:
    """Execute all migrations in order, then seed system prompts."""
    async with pool.acquire() as conn:
        for i, sql in enumerate(MIGRATIONS):
            await conn.execute(sql)
        logger.info(f"Database migrations completed ({len(MIGRATIONS)} statements)")

        # Seed system prompts (idempotent)
        for seed in SEED_PROMPTS:
            await conn.execute(
                """
                INSERT INTO system_prompts (mode, prompt_template)
                VALUES ($1, $2)
                ON CONFLICT (mode) DO NOTHING
                """,
                seed["mode"],
                seed["prompt_template"],
            )
        logger.info(f"System prompts seeded ({len(SEED_PROMPTS)} entries)")
