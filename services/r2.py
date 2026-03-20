"""
Sakhi — Cloudflare R2 Storage Client
======================================
Handles uploading images to Cloudflare R2 (S3-compatible) and returning
public URLs. Uses the lazy-singleton pattern from ``db/pool.py``.

Key prefixes:
  - ``seeds/swys/{image_id}.webp``          — permanent SWYS seed images
  - ``cache/gentype/{theme_id}/{LETTER}.webp`` — themed alphabet (30-day expiry via R2 lifecycle)
"""

import asyncio
import logging
import os

import boto3
import httpx

logger = logging.getLogger("sakhi.r2")

_client: "R2Client | None" = None


class R2Client:
    """Thin async wrapper around boto3 S3 client for Cloudflare R2."""

    def __init__(self) -> None:
        account_id = os.environ["R2_ACCOUNT_ID"]
        self._bucket = os.environ.get("R2_BUCKET_NAME", "sakhi-media")
        self._public_url_base = os.environ["R2_PUBLIC_URL"].rstrip("/")

        self._s3 = boto3.client(
            "s3",
            endpoint_url=f"https://{account_id}.r2.cloudflarestorage.com",
            aws_access_key_id=os.environ["R2_ACCESS_KEY_ID"],
            aws_secret_access_key=os.environ["R2_SECRET_ACCESS_KEY"],
            region_name="auto",
        )

    async def upload_from_url(
        self,
        source_url: str,
        r2_key: str,
        content_type: str = "image/webp",
    ) -> str:
        """Download image from *source_url* and upload to R2. Returns public URL."""
        async with httpx.AsyncClient() as client:
            resp = await client.get(source_url, follow_redirects=True, timeout=30)
            resp.raise_for_status()
            data = resp.content

        return await self.upload_bytes(data, r2_key, content_type)

    async def upload_bytes(
        self,
        data: bytes,
        r2_key: str,
        content_type: str = "image/webp",
    ) -> str:
        """Upload raw bytes to R2. Returns public URL."""
        await asyncio.to_thread(
            self._s3.put_object,
            Bucket=self._bucket,
            Key=r2_key,
            Body=data,
            ContentType=content_type,
        )
        url = self.public_url(r2_key)
        logger.info("Uploaded %s (%d bytes)", r2_key, len(data))
        return url

    def public_url(self, r2_key: str) -> str:
        """Return the public URL for an R2 key."""
        return f"{self._public_url_base}/{r2_key}"


# ── Key builder helpers ──────────────────────────────────────────────


def swys_seed_key(image_id: str) -> str:
    return f"seeds/swys/{image_id}.webp"


def gentype_cache_key(theme_id: str, letter: str) -> str:
    return f"cache/gentype/{theme_id}/{letter.upper()}.webp"


# ── Singleton ────────────────────────────────────────────────────────


def get_r2_client() -> R2Client:
    """Return (or create) the module-level R2Client singleton."""
    global _client
    if _client is None:
        _client = R2Client()
    return _client
