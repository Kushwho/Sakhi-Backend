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
    # NOTE: 384-dim — matches all-MiniLM-L6-v2 via sentence-transformers
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
    # ------- unique constraint: one summary row per (profile, thread) -------
    # Required for ON CONFLICT upsert in session_summarizer so that resuming
    # and ending an existing conversation updates the row instead of duplicating it.
    """
    CREATE UNIQUE INDEX IF NOT EXISTS uq_session_summaries_profile_room
        ON session_summaries(profile_id, room_name);
    """,
    # ------- swys_images (Say What You See seed image catalog) -------
    """
    CREATE TABLE IF NOT EXISTS swys_images (
        id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        title           TEXT NOT NULL,
        original_prompt TEXT NOT NULL,
        image_url       TEXT NOT NULL,
        level           SMALLINT NOT NULL CHECK (level BETWEEN 1 AND 5),
        category        TEXT NOT NULL DEFAULT 'general',
        is_active       BOOLEAN NOT NULL DEFAULT true,
        created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
        CONSTRAINT swys_images_prompt_unique UNIQUE (original_prompt)
    );
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_swys_images_level
        ON swys_images(level, is_active);
    """,
    # ------- swys_attempts (kid's image-prompt attempts) -------
    """
    CREATE TABLE IF NOT EXISTS swys_attempts (
        id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        profile_id          UUID NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
        image_id            UUID NOT NULL REFERENCES swys_images(id),
        kid_prompt          TEXT NOT NULL,
        generated_image_url TEXT NOT NULL,
        score               SMALLINT NOT NULL CHECK (score BETWEEN 0 AND 100),
        hint                TEXT NOT NULL,
        created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
    );
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_swys_attempts_profile
        ON swys_attempts(profile_id, created_at DESC);
    """,
    # ------- gentype_cache (GenType letter image cache) -------
    """
    CREATE TABLE IF NOT EXISTS gentype_cache (
        id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        cache_key   TEXT UNIQUE NOT NULL,
        letter      CHAR(1) NOT NULL,
        theme_id    TEXT NOT NULL,
        image_url   TEXT NOT NULL,
        created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
    );
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_gentype_cache_key
        ON gentype_cache(cache_key);
    """,
    # ------- stories (Story Narration Agent) -------
    """
    CREATE TABLE IF NOT EXISTS stories (
        id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        title          TEXT NOT NULL,
        genre          TEXT NOT NULL DEFAULT 'general',
        age_min        INT NOT NULL DEFAULT 4,
        age_max        INT NOT NULL DEFAULT 12,
        language       TEXT NOT NULL DEFAULT 'English',
        total_segments INT NOT NULL DEFAULT 1,
        created_at     TIMESTAMPTZ NOT NULL DEFAULT now()
    );
    """,
    # ------- story_segments -------
    """
    CREATE TABLE IF NOT EXISTS story_segments (
        id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        story_id   UUID NOT NULL REFERENCES stories(id) ON DELETE CASCADE,
        position   INT NOT NULL,
        content    TEXT NOT NULL,
        UNIQUE(story_id, position)
    );
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_story_segments_lookup
        ON story_segments(story_id, position);
    """,
    # ------- stories: user-generated story persistence columns -------
    """
    ALTER TABLE stories ADD COLUMN IF NOT EXISTS
        profile_id UUID REFERENCES profiles(id) ON DELETE CASCADE;
    """,
    """
    ALTER TABLE stories ADD COLUMN IF NOT EXISTS idea TEXT;
    """,
    """
    ALTER TABLE stories ADD COLUMN IF NOT EXISTS
        scenes_payload JSONB DEFAULT '[]';
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_stories_profile
        ON stories(profile_id, created_at DESC);
    """,
    # ------- chat_image_usage (daily quota tracking for in-chat image gen) -------
    """
    CREATE TABLE IF NOT EXISTS chat_image_usage (
        id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        profile_id  UUID NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
        created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
    );
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_chat_image_usage_profile_day
        ON chat_image_usage(profile_id, created_at DESC);
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
            '4. End with "What do you think?" or a follow-up wonder question\n\n'
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
            '"{surprise_fact}"\n\n'
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
    {
        "mode": "story_writer",
        "prompt_template": (
            "You are a world-class children's story writer specialising in Indian children aged 4-12.\n\n"
            "Your task is to write a vivid, imaginative, age-appropriate short story based on the user's idea.\n\n"
            "STRICT RULES:\n"
            "1. Output ONLY valid JSON. No prose, no explanation, no markdown fences.\n"
            "2. The JSON must conform EXACTLY to this schema:\n"
            "   {\n"
            '     "title": "string - a short, catchy story title",\n'
            '     "scenes": [\n'
            "       {\n"
            '         "story_text": "string - one full narrative paragraph (60-120 words). Expressive, child-friendly language.",\n'
            '         "image_prompt": "string - a HIGHLY DETAILED visual prompt for an illustration of this exact scene. '
            "Include: art style, mood, colours, characters, setting, action. "
            "Example: 'Vibrant gouache illustration of a brave 8-year-old Indian girl with braids, wearing a red kurti, "
            "standing at the edge of a misty rainforest, holding a glowing lantern, wide-eyed with wonder, lush green "
            "canopy above, fireflies in background, warm amber light, storybook style, rich saturated colours.'\"\n"
            "       }\n"
            "     ]\n"
            "   }\n\n"
            "3. The story must be child-safe - no violence, no inappropriate content, ever.\n"
            "4. Calibrate vocabulary and complexity to the child's age (provided by the user).\n"
            "5. Each scene's image_prompt must be self-contained and visually specific - "
            "describe it as if the artist has never read the story.\n"
            "6. Do NOT include any text outside the JSON object."
        ),
    },
    {
        "mode": "story_ssml",
        "prompt_template": (
            "You are a voice-acting director for a children's story narration engine.\n\n"
            "Your job is to take a plain story paragraph and add expressive markup tags so that "
            "the TTS engine reads it with emotion, pauses, and natural delivery.\n\n"
            "AVAILABLE TAGS:\n"
            "- Emotions: [happy], [sad], [angry], [surprised], [fearful], [disgusted]\n"
            "- Delivery styles: [laughing], [whispering]\n"
            "- Non-verbal sounds: [breathe], [clear_throat], [cough], [laugh], [sigh], [yawn]\n"
            '- Pauses: <break time="1s" />, <break time="500ms" />\n\n'
            "RULES:\n"
            "1. Output ONLY the marked-up text. No explanations, no quotes, no preamble.\n"
            "2. Do NOT change any words in the original text. Only INSERT tags.\n"
            "3. Place emotion/delivery tags BEFORE the sentence or phrase they apply to.\n"
            "4. Use pauses at natural story beats - scene transitions, dramatic moments, dialogue boundaries.\n"
            "5. Do NOT over-tag. Use 2-5 tags per paragraph. Less is more.\n"
            "6. Non-verbal sounds should feel natural - a [sigh] before a sad moment, a [laugh] during a funny line.\n\n"
            "FEW-SHOT EXAMPLES:\n\n"
            "---\n"
            'Input: Rani looked up at the tall, tall mountain. "I can do this," she whispered to herself. '
            "She took a deep breath and began to climb.\n"
            'Output: Rani looked up at the tall, tall mountain. <break time="500ms" />[whispering] '
            '"I can do this," she whispered to herself. <break time="500ms" />[breathe] '
            "She took a deep breath and began to climb.\n"
            "---\n"
            "Input: The monkey swung from tree to tree, laughing as the birds chased him. "
            '"You can\'t catch me!" he shouted. But then he slipped and tumbled into the river with a big splash!\n'
            "Output: [happy] The monkey swung from tree to tree, [laughing] laughing as the birds chased him. "
            '"You can\'t catch me!" he shouted. <break time="500ms" />[surprised] '
            "But then he slipped and tumbled into the river with a big splash!\n"
            "---\n"
            "Input: The forest was dark and quiet. Arjun could hear his own heartbeat. "
            "Somewhere far away, an owl hooted. He wanted to go home.\n"
            'Output: The forest was dark and quiet. <break time="500ms" />[fearful] '
            'Arjun could hear his own heartbeat. <break time="1s" />Somewhere far away, an owl hooted. '
            "[sad] He wanted to go home.\n"
            "---\n"
            'Input: "We did it!" cheered Maya, jumping up and down. The whole village came out to celebrate. '
            "There was music, dancing, and the biggest feast anyone had ever seen.\n"
            'Output: [happy] "We did it!" cheered Maya, jumping up and down. <break time="500ms" />'
            "The whole village came out to celebrate. [laughing] There was music, dancing, and the biggest "
            "feast anyone had ever seen.\n"
            "---"
        ),
    },
]


async def run_migrations(pool: asyncpg.Pool) -> None:
    """Execute all migrations in order, then seed system prompts."""
    async with pool.acquire() as conn:
        for _i, sql in enumerate(MIGRATIONS):
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