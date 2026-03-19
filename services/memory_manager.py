"""
Sakhi — Memory Manager
========================
Generic long-term memory service that extracts, stores, deduplicates,
and recalls memories using pgvector in NeonDB.

Inspired by langmem concepts but implemented from scratch.

Namespace isolation: (service, profile_id) — different products
(sakhi, story_agent, study_agent) get their own memory space.

Usage:
    memory_mgr = MemoryManager()
    # Background extraction (post-session):
    await memory_mgr.extract_and_store(profile_id, "sakhi", transcript)
    # Real-time recall (in-session):
    memories = await memory_mgr.recall(profile_id, "sakhi", "dinosaurs")
"""

import json
import logging
import os
import uuid
from datetime import datetime, timezone
from typing import Literal

import asyncpg
import replicate
from groq import AsyncGroq
from pydantic import BaseModel, ValidationError, field_validator

logger = logging.getLogger("sakhi.memory")


# ---------------------------------------------------------------------------
# Extraction prompt
# ---------------------------------------------------------------------------

EXTRACT_MEMORIES_PROMPT = """\
You are analyzing a conversation between an AI companion (Sakhi) and a child.
Extract ONLY memories that would genuinely help personalize future conversations.

Be VERY selective. Only extract something if knowing it would change how Sakhi \
talks to this child next time.

EXTRACT (max 5 memories per conversation):
- Specific interests and passions (e.g. "loves dinosaurs, especially T-Rex")
- Family/friend names and relationships (e.g. "has a sister named Priya")
- Strong emotional reactions to topics (e.g. "gets anxious talking about exams")
- Learning breakthroughs (e.g. "finally understood fractions using pizza slices")
- Unique personal facts (e.g. "wants to be an astronaut when growing up")

DO NOT EXTRACT:
- The child's name, age, or language preference (already known)
- Generic facts like "likes talking" or "had a conversation"
- Things Sakhi said or suggested (only extract what the CHILD revealed)
- Anything vague or obvious (e.g. "is a student", "goes to school")
- Places visited during the conversation unless deeply meaningful
- Restatements of what was discussed without personal significance

Return a JSON object with a "memories" key containing an array. Each memory:
- "content": One concise sentence about the child (e.g. "Loves building Lego spaceships")
- "category": One of "interest", "family", "emotion", "learning", "aspiration"

Return {{"memories": []}} if nothing genuinely personalizing was revealed.
Return ONLY the JSON object, no markdown.

TRANSCRIPT:
{transcript}
"""

MERGE_MEMORY_PROMPT = """\
You have an existing memory about a child and a new piece of information from a conversation.
Merge them into a single, concise statement that preserves ALL details from both.

Rules:
- Keep it to 1-2 sentences max
- Preserve specific details (names, favorites, emotions)
- If the new info contradicts the old, prefer the new info (more recent)
- Do NOT add any commentary — return ONLY the merged memory text

EXISTING MEMORY: {old_content}
NEW INFORMATION: {new_content}

MERGED MEMORY:"""


# ---------------------------------------------------------------------------
# Pydantic schema for LLM memory output
# ---------------------------------------------------------------------------

MemoryCategory = Literal["interest", "family", "emotion", "learning", "aspiration"]


class ExtractedMemory(BaseModel):
    """Validates and sanitizes a single memory extracted by the LLM."""

    content: str
    category: MemoryCategory = "interest"  # default if missing/invalid

    @field_validator("content")
    @classmethod
    def content_not_empty(cls, v: str) -> str:
        v = v.strip()
        if len(v) < 5:
            raise ValueError(f"Content too short: {v!r}")
        return v


# ---------------------------------------------------------------------------
# MemoryManager
# ---------------------------------------------------------------------------


class MemoryManager:
    """Generic long-term memory service for Sakhi products.

    Handles embedding generation, LLM-based extraction, deduplication
    via cosine similarity, and recall via pgvector search.

    Subclass this to customize extraction for different products:
        class StudyMemoryManager(MemoryManager):
            EXTRACTION_PROMPT = "...(study-focused prompt)..."
    """

    EXTRACTION_PROMPT = EXTRACT_MEMORIES_PROMPT
    SIMILARITY_THRESHOLD = 0.85  # above this → near-duplicate, reinforce only
    UPDATE_THRESHOLD = 0.6       # between this and SIMILARITY → LLM merge
    DEFAULT_SERVICE = "sakhi"
    MEMORY_TTL_DAYS = 30

    def __init__(self):
        self._db_pool: asyncpg.Pool | None = None

    # ── Database pool (lazy-init, separate from FastAPI) ────────────────

    async def _get_pool(self) -> asyncpg.Pool | None:
        """Lazy-init a small asyncpg pool for the agent process."""
        if self._db_pool is None:
            database_url = os.getenv("DATABASE_URL")
            if not database_url:
                logger.warning("DATABASE_URL not set — memory persistence disabled")
                return None
            self._db_pool = await asyncpg.create_pool(
                dsn=database_url, min_size=1, max_size=3
            )
            logger.info("MemoryManager DB pool created")
        return self._db_pool

    # ── Embedding ──────────────────────────────────────────────────────

    async def generate_embedding(self, text: str) -> list[float]:
        """Generate a 768-dim embedding vector via Replicate API."""
        output = await replicate.async_run(
            "replicate/all-mpnet-base-v2:b6b7585c9640cd7a9572c6e129c9549d79c9c31f0d3fdce7baac7c67ca38f305",
            input={"text": text},
        )
        # output is a list of dicts with "embedding" key, or a flat list
        if isinstance(output, list) and output and isinstance(output[0], dict):
            return output[0]["embedding"]
        return list(output)

    # ── Extraction (background, post-session) ──────────────────────────

    async def extract_and_store(
        self,
        profile_id: str,
        service: str,
        transcript: list[dict],
    ) -> list[dict]:
        """Extract memories from a transcript and store them in NeonDB.

        This runs in the background after a session ends.

        Args:
            profile_id: Child's profile UUID.
            service: Namespace service type (e.g. "sakhi", "study_agent").
            transcript: List of {"role": ..., "text": ...} dicts.

        Returns:
            List of extracted memory dicts that were stored.
        """
        if not transcript:
            logger.info("Empty transcript — skipping memory extraction")
            return []

        # 1. LLM call to extract memories
        raw_memories = await self._call_extraction_llm(transcript)
        if not raw_memories:
            logger.info("No memories extracted from transcript")
            return []

        logger.info(f"Extracted {len(raw_memories)} candidate memories")

        # 2. For each memory: embed → deduplicate → store
        stored = []
        pool = await self._get_pool()
        if not pool:
            return []

        for mem in raw_memories:
            content = mem.get("content", "").strip()
            if not content or len(content) < 5:
                continue

            try:
                embedding = await self.generate_embedding(content)
                was_new = await self._deduplicate_and_store(
                    pool=pool,
                    profile_id=profile_id,
                    service=service,
                    content=content,
                    embedding=embedding,
                    metadata={"category": mem.get("category", "other")},
                )
                stored.append({
                    "content": content,
                    "category": mem.get("category", "other"),
                    "is_new": was_new,
                })
            except Exception as e:
                logger.warning(f"Failed to store memory '{content[:50]}': {e}")

        new_count = sum(1 for m in stored if m["is_new"])
        reinforced_count = len(stored) - new_count
        logger.info(
            f"Memory extraction complete: {new_count} new, "
            f"{reinforced_count} reinforced, {len(raw_memories) - len(stored)} skipped"
        )
        return stored

    # ── Recall (real-time, in-session) ─────────────────────────────────

    async def recall(
        self,
        profile_id: str,
        service: str,
        query: str,
        limit: int = 5,
    ) -> list[str]:
        """Recall relevant memories for a given query via pgvector search.

        Ranking blends cosine similarity (70%) and memory strength (30%) so
        frequently-reinforced memories rank slightly higher when relevance is
        equal. Memories older than MEMORY_TTL_DAYS are excluded entirely.

        Args:
            profile_id: Child's profile UUID.
            service: Namespace service type.
            query: Text to search for similar memories.
            limit: Max number of memories to return.

        Returns:
            List of memory content strings, ranked by relevance.
        """
        pool = await self._get_pool()
        if not pool:
            return []

        try:
            query_embedding = await self.generate_embedding(query)
            embedding_str = "[" + ",".join(str(x) for x in query_embedding) + "]"

            async with pool.acquire() as conn:
                # Increase IVFFlat probes for this query — trades a bit of speed
                # for better recall quality (default probe=1 misses close neighbors)
                await conn.execute("SET LOCAL ivfflat.probes = 10")

                rows = await conn.fetch(
                    """
                    SELECT content,
                           1 - (embedding <=> $1::vector) AS similarity,
                           strength
                    FROM memories
                    WHERE service = $2
                      AND profile_id = $3
                      AND updated_at > NOW() - ($4 * INTERVAL '1 day')
                    ORDER BY
                        -- Blended score: 70% similarity + 30% normalised strength
                        -- Higher is better, so DESC
                        (1 - (embedding <=> $1::vector)) * 0.7
                        + (LEAST(strength, 10.0) / 10.0) * 0.3
                        DESC
                    LIMIT $5
                    """,
                    embedding_str,
                    service,
                    uuid.UUID(profile_id),
                    float(self.MEMORY_TTL_DAYS),
                    limit,
                )

            if not rows:
                return []

            # Hard floor: drop anything with < 0.3 cosine similarity (pure noise)
            results = [row["content"] for row in rows if row["similarity"] >= 0.3]

            logger.debug(
                f"Recalled {len(results)} memories for query='{query[:50]}' "
                f"(service={service}, profile={profile_id[:8]}, "
                f"ttl={self.MEMORY_TTL_DAYS}d)"
            )
            return results

        except Exception as e:
            logger.warning(f"Memory recall failed: {e}")
            return []

    # ── Internals ──────────────────────────────────────────────────────

    async def _call_extraction_llm(self, transcript: list[dict]) -> list[dict]:
        """Call Groq LLM to extract memories from a transcript."""
        try:
            # Format transcript
            lines = []
            for msg in transcript:
                role = msg.get("role", "unknown")
                text = msg.get("text", "")
                if role == "system":
                    continue
                speaker = "CHILD" if role == "user" else "SAKHI"
                lines.append(f"{speaker}: {text}")
            transcript_text = "\n".join(lines) if lines else "No conversation content."

            client = AsyncGroq()
            prompt = self.EXTRACTION_PROMPT.format(transcript=transcript_text)

            response = await client.chat.completions.create(
                model="llama-3.1-8b-instant",
                messages=[{"role": "user", "content": prompt}],
                temperature=0.3,
                max_tokens=800,
                response_format={"type": "json_object"},
            )

            raw = response.choices[0].message.content
            result = json.loads(raw)

            # Unwrap to a list regardless of response shape
            candidates: list = []
            if isinstance(result, list):
                candidates = result
            elif isinstance(result, dict):
                for key in ("memories", "results", "items", "data"):
                    if key in result and isinstance(result[key], list):
                        candidates = result[key]
                        break
                else:
                    # Single memory object returned directly
                    if "content" in result:
                        candidates = [result]

            return self._validate_memories(candidates)

        except json.JSONDecodeError as e:
            logger.error(f"Memory extraction returned invalid JSON: {e}")
            return []
        except Exception as e:
            logger.error(f"Memory extraction LLM call failed: {e}")
            return []

    def _validate_memories(self, candidates: list) -> list[dict]:
        """Validate raw LLM output using the ExtractedMemory Pydantic model.

        Invalid items are dropped and logged; one bad item never blocks the rest.
        """
        valid = []
        for i, item in enumerate(candidates):
            try:
                memory = ExtractedMemory.model_validate(item)
                valid.append(memory.model_dump())
            except ValidationError as e:
                logger.warning(f"Memory[{i}] failed validation — skipped: {e.errors()}")

        logger.debug(f"Memory validation: {len(valid)}/{len(candidates)} passed")
        return valid

    async def _merge_memory_content(self, old_content: str, new_content: str) -> str:
        """Use the LLM to merge an existing memory with new information.

        Returns the merged text, or the new_content as-is if the LLM call fails.
        """
        try:
            client = AsyncGroq()
            prompt = MERGE_MEMORY_PROMPT.format(
                old_content=old_content,
                new_content=new_content,
            )
            response = await client.chat.completions.create(
                model="llama-3.1-8b-instant",
                messages=[{"role": "user", "content": prompt}],
                temperature=0.2,
                max_tokens=200,
            )
            merged = response.choices[0].message.content.strip()
            if len(merged) < 5:
                return new_content
            logger.debug(f"Merged memory: '{old_content[:40]}' + '{new_content[:40]}' → '{merged[:60]}'")
            return merged
        except Exception as e:
            logger.warning(f"Memory merge LLM call failed, keeping new content: {e}")
            return new_content

    async def _deduplicate_and_store(
        self,
        pool: asyncpg.Pool,
        profile_id: str,
        service: str,
        content: str,
        embedding: list[float],
        metadata: dict | None = None,
    ) -> bool:
        """Three-tier dedup: reinforce near-dupes, merge related, insert new.

        | Similarity        | Action                                     |
        |-------------------|--------------------------------------------|
        | ≥ 0.85            | Reinforce — bump strength only             |
        | 0.6 – 0.85        | Merge — LLM combines old+new, re-embed     |
        | < 0.6             | Insert — brand new memory                  |

        Returns True if a new memory was inserted, False otherwise.
        """
        embedding_str = "[" + ",".join(str(x) for x in embedding) + "]"

        async with pool.acquire() as conn:
            # Find the most similar existing memory in this namespace
            row = await conn.fetchrow(
                """
                SELECT id, content, strength,
                       1 - (embedding <=> $1::vector) AS similarity
                FROM memories
                WHERE service = $2 AND profile_id = $3
                ORDER BY embedding <=> $1::vector
                LIMIT 1
                """,
                embedding_str,
                service,
                uuid.UUID(profile_id),
            )

            sim = row["similarity"] if row else 0.0

            if sim >= self.SIMILARITY_THRESHOLD:
                # ── Near-duplicate: just reinforce ──────────────────────
                new_strength = min(row["strength"] + 0.5, 10.0)
                await conn.execute(
                    """
                    UPDATE memories
                    SET strength = $1, updated_at = $2
                    WHERE id = $3
                    """,
                    new_strength,
                    datetime.now(timezone.utc),
                    row["id"],
                )
                logger.debug(
                    f"Reinforced memory (sim={sim:.2f}): "
                    f"'{row['content'][:50]}' → strength={new_strength}"
                )
                return False

            elif sim >= self.UPDATE_THRESHOLD:
                # ── Related: merge via LLM ─────────────────────────────
                merged_content = await self._merge_memory_content(
                    old_content=row["content"],
                    new_content=content,
                )
                merged_embedding = await self.generate_embedding(merged_content)
                merged_emb_str = "[" + ",".join(str(x) for x in merged_embedding) + "]"
                new_strength = min(row["strength"] + 1.0, 10.0)

                await conn.execute(
                    """
                    UPDATE memories
                    SET content = $1,
                        embedding = $2::vector,
                        strength = $3,
                        updated_at = $4
                    WHERE id = $5
                    """,
                    merged_content,
                    merged_emb_str,
                    new_strength,
                    datetime.now(timezone.utc),
                    row["id"],
                )
                logger.info(
                    f"Merged memory (sim={sim:.2f}): "
                    f"'{row['content'][:40]}' + '{content[:40]}' "
                    f"→ '{merged_content[:60]}'"
                )
                return False

            else:
                # ── New memory: insert ─────────────────────────────────
                await conn.execute(
                    """
                    INSERT INTO memories
                        (profile_id, service, content, embedding, metadata, strength)
                    VALUES ($1, $2, $3, $4::vector, $5, 1.0)
                    """,
                    uuid.UUID(profile_id),
                    service,
                    content,
                    embedding_str,
                    json.dumps(metadata or {}),
                )
                logger.debug(f"Inserted new memory: '{content[:50]}'")
                return True

