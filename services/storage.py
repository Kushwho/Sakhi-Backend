"""
Sakhi — Google Cloud Storage Service
====================================
Provides a unified interface to upload files from URLs or memory to a GCP bucket.
This allows us to cache assets (like Replicate images and generated audio) locally
in our cloud rather than relying on ephemeral URLs.

Requires environment variables:
- GCP_BUCKET_NAME
- GOOGLE_APPLICATION_CREDENTIALS (if running outside GCP with a service account file)
"""

import logging
import os
import uuid
from typing import Optional

import httpx
from google.cloud import storage
from google.api_core.exceptions import GoogleAPIError

logger = logging.getLogger("sakhi.storage")

class GCPStorageService:
    def __init__(self) -> None:
        self.bucket_name = os.getenv("GCP_BUCKET_NAME")
        if not self.bucket_name:
            logger.warning("GCP_BUCKET_NAME is not set. Storage uploads will be skipped.")
            self.client = None
            self.bucket = None
        else:
            try:
                self.client = storage.Client()
                self.bucket = self.client.bucket(self.bucket_name)
            except Exception as e:
                logger.error(f"Failed to initialize GCP Storage Client: {e}", exc_info=True)
                self.client = None
                self.bucket = None

    async def upload_from_url(
        self, url: str, destination_folder: str, file_ext: str
    ) -> Optional[str]:
        """
        Download a file from an external URL and upload it directly to the GCP bucket.
        Useful for saving images/audio returned by Replicate.

        Args:
            url: The source URL to download from.
            destination_folder: Folder path within the bucket (e.g., 'story_images').
            file_ext: File extension (e.g., '.webp', '.mp3'). Must include the dot.

        Returns:
            The public URL of the uploaded object, or None if the upload failed.
        """
        if not self.bucket:
            logger.error("Cannot upload: GCP_BUCKET_NAME is not configured or client failed to initialize.")
            return url  # Fallback to the original URL so the app doesn't break

        # Generate a unique path
        filename = f"{uuid.uuid4().hex}{file_ext}"
        blob_path = f"{destination_folder}/{filename}"

        logger.info(f"Downloading media from {url} to upload to gs://{self.bucket_name}/{blob_path}")

        try:
            # Download the file content
            async with httpx.AsyncClient(timeout=30.0) as http_client:
                response = await http_client.get(url)
                response.raise_for_status()
                content = response.content

            # Upload to Google Cloud Storage (in an executor thread to avoid blocking asyncio)
            blob = self.bucket.blob(blob_path)
            
            import asyncio
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(
                None,
                lambda: blob.upload_from_string(
                    content, content_type=response.headers.get("Content-Type")
                )
            )

            # Return the public URL
            public_url = f"https://storage.googleapis.com/{self.bucket_name}/{blob_path}"
            logger.info(f"Successfully uploaded media to GCP: {public_url}")
            return public_url

        except httpx.HTTPError as e:
            logger.error(f"HTTP error while downloading {url} for GCP upload: {e}")
            return None
        except GoogleAPIError as e:
            logger.error(f"GCP API error while uploading to gs://{self.bucket_name}/{blob_path}: {e}")
            return None
        except Exception as e:
            logger.error(f"Unexpected error uploading to GCP: {e}", exc_info=True)
            return None

# Singleton instance
_storage_service: Optional[GCPStorageService] = None

def get_storage_service() -> GCPStorageService:
    global _storage_service
    if _storage_service is None:
        _storage_service = GCPStorageService()
    return _storage_service
