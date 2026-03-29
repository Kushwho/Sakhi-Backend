"""
Google OAuth Service
==================
Verifies Google ID tokens using Google's public keys.
"""

import asyncio
import json
import logging
from typing import Dict, Optional
from datetime import datetime, timedelta

import httpx
import jwt
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.backends import default_backend
import base64

logger = logging.getLogger("sakhi.google_oauth")

# Google's JWKS (JSON Web Key Set) URL
GOOGLE_CERTS_URL = "https://www.googleapis.com/oauth2/v3/certs"
GOOGLE_ISSUERS = ["accounts.google.com", "https://accounts.google.com"]

# Cache for Google's public keys (refreshes every hour)
_key_cache: Dict[str, rsa.RSAPublicKey] = {}
_cache_timestamp: Optional[datetime] = None
CACHE_TTL = timedelta(hours=1)


def jwk_to_rsa_key(jwk_data: Dict) -> rsa.RSAPublicKey:
    """
    Convert a JWK (JSON Web Key) to an RSA public key.

    Args:
        jwk_data: Dictionary containing JWK components

    Returns:
        RSAPublicKey object
    """
    # Decode the modulus (n) and exponent (e) from base64url
    def base64url_to_bytes(data: str) -> bytes:
        # Add padding if necessary
        padding = len(data) % 4
        if padding:
            data += '=' * (4 - padding)
        # Convert base64url to base64
        data = data.replace('-', '+').replace('_', '/')
        return base64.b64decode(data)

    n = int.from_bytes(base64url_to_bytes(jwk_data['n']), 'big')
    e = int.from_bytes(base64url_to_bytes(jwk_data['e']), 'big')

    # Construct the RSA public key
    public_numbers = rsa.RSAPublicNumbers(e, n)
    public_key = public_numbers.public_key(backend=default_backend())

    return public_key


async def get_google_public_keys() -> Dict[str, rsa.RSAPublicKey]:
    """
    Fetch Google's JWKS (JSON Web Key Set) and cache it.

    Returns:
        Dictionary mapping key IDs to RSA public key objects
    """
    global _key_cache, _cache_timestamp

    now = datetime.now()

    # Return cached keys if still valid
    if _key_cache and _cache_timestamp and (now - _cache_timestamp) < CACHE_TTL:
        logger.debug("Using cached Google public keys")
        return _key_cache

    # Fetch fresh keys from Google
    try:
        response = httpx.get(GOOGLE_CERTS_URL, timeout=10.0)
        response.raise_for_status()
        certs_data = response.json()

        # Parse JWKS into dictionary of RSA public keys
        keys = {}
        for key_data in certs_data.get("keys", []):
            key_id = key_data.get("kid")
            if key_id:
                try:
                    public_key = jwk_to_rsa_key(key_data)
                    keys[key_id] = public_key
                except Exception as e:
                    logger.warning(f"Failed to convert key {key_id}: {e}")
                    continue

        # Update cache
        _key_cache = keys
        _cache_timestamp = now
        logger.info(f"Fetched {len(keys)} Google public keys")

        return keys

    except httpx.HTTPStatusError as e:
        logger.error(f"Failed to fetch Google certificates: {e}")
        if _key_cache:
            logger.warning("Using stale cached keys due to fetch error")
            return _key_cache
        raise
    except Exception as e:
        logger.error(f"Unexpected error fetching Google certificates: {e}")
        if _key_cache:
            logger.warning("Using stale cached keys due to error")
            return _key_cache
        raise


async def verify_google_token(id_token: str) -> Dict:
    """
    Verify a Google ID token and return its payload.

    Args:
        id_token: Google ID token from client

    Returns:
        Dictionary containing:
            - google_id: User's Google ID (sub claim)
            - email: User's email address
            - name: User's display name
            - email_verified: Whether email is verified by Google

    Raises:
        ValueError: If token is invalid, expired, or malformed
    """
    try:
        # Get Google's public keys
        keys = await get_google_public_keys()

        # Decode JWT header to get key ID (kid)
        try:
            header = jwt.get_unverified_header(id_token)
            key_id = header.get("kid")
        except Exception as e:
            logger.error(f"Error decoding token headers: {e}")
            raise ValueError("Invalid token format: could not decode headers")

        if not key_id:
            raise ValueError("Token missing key ID")

        # Find the key that matches the token
        public_key = keys.get(key_id)
        if not public_key:
            logger.error(f"Key {key_id} not found in Google's public keys")
            raise ValueError("Invalid token key ID")

        # Verify the token using the public key
        try:
            payload = jwt.decode(
                id_token,
                key=public_key,
                algorithms=["RS256"],
                audience=None,  # We'll verify with CLIENT_ID in account creation
                issuer=GOOGLE_ISSUERS,
                options={
                    "verify_aud": False,  # Skip aud verification here, do in account service
                    "verify_iss": True,
                    "verify_exp": True,
                },
            )
        except jwt.ExpiredSignatureError:
            raise ValueError("Google token has expired")
        except jwt.InvalidTokenError as e:
            logger.error(f"JWT validation error: {e}")
            raise ValueError(f"Invalid Google token: {str(e)}")
        except Exception as e:
            logger.error(f"Unexpected error decoding token: {e}")
            raise ValueError(f"Failed to verify Google token: {str(e)}")

        # Validate required claims
        if "sub" not in payload:
            raise ValueError("Token missing subject claim")

        if "email" not in payload:
            raise ValueError("Token missing email claim")

        # Extract user information
        return {
            "google_id": payload["sub"],
            "email": payload["email"],
            "name": payload.get("name", ""),
            "email_verified": payload.get("email_verified", False),
            "picture": payload.get("picture"),
        }

    except Exception as e:
        logger.error(f"Unexpected error verifying Google token: {e}")
        raise ValueError(f"Failed to verify Google token: {str(e)}")
