"""
Sakhi Backend — MSG91 Email OTP Service
=========================================
Send OTP emails via the MSG91 Email Send API and verify them locally.

Uses POST https://control.msg91.com/api/v5/email/send to deliver OTP emails.
OTP generation and verification are handled server-side since the email
send API does not include built-in OTP lifecycle management.
"""

import logging
import os
import random
import time

import httpx

logger = logging.getLogger("sakhi.msg91")

MSG91_EMAIL_URL = "https://control.msg91.com/api/v5/email/send"

# In-memory OTP store: { email_lowercase: { "otp": str, "expires_at": float } }
_otp_store: dict[str, dict] = {}


def _get_auth_key() -> str:
    key = os.getenv("MSG91_AUTH_KEY")
    if not key:
        raise RuntimeError("MSG91_AUTH_KEY environment variable is not set")
    return key


def _get_template_id() -> str:
    tid = os.getenv("MSG91_TEMPLATE_ID")
    if not tid:
        raise RuntimeError("MSG91_TEMPLATE_ID environment variable is not set")
    return tid


def _get_sender_email() -> str:
    email = os.getenv("MSG91_SENDER_EMAIL", "")
    if not email:
        raise RuntimeError("MSG91_SENDER_EMAIL environment variable is not set")
    return email


def _get_sender_name() -> str:
    return os.getenv("MSG91_SENDER_NAME", "Sakhi")


def _get_domain() -> str:
    domain = os.getenv("MSG91_DOMAIN", "")
    if not domain:
        raise RuntimeError("MSG91_DOMAIN environment variable is not set")
    return domain


def _generate_otp(length: int = 6) -> str:
    """Generate a random numeric OTP of the given length."""
    return "".join(str(random.randint(0, 9)) for _ in range(length))


async def send_otp(email: str) -> None:
    """Generate a 6-digit OTP and send it to the given email via MSG91 Email API.

    Raises ValueError on API errors.
    """
    otp = _generate_otp(6)
    expires_at = time.time() + 300  # 5 minutes

    # Store OTP for later verification
    _otp_store[email.lower()] = {"otp": otp, "expires_at": expires_at}

    headers = {
        "authkey": _get_auth_key(),
        "Content-Type": "application/json",
        "accept": "application/json",
    }
    body = {
        "recipients": [
            {
                "to": [{"email": email}],
                "variables": {
                    "otp": otp,
                    "company_name": os.getenv("MSG91_COMPANY_NAME", "Playla"),
                    },
            }
        ],
        "from": {
            "name": _get_sender_name(),
            "email": _get_sender_email(),
        },
        "domain": _get_domain(),
        "template_id": _get_template_id(),
    }

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(MSG91_EMAIL_URL, headers=headers, json=body)
            logger.debug(f"MSG91 send_otp response: status={resp.status_code} body={resp.text}")
    except httpx.HTTPError as exc:
        logger.error(f"MSG91 send_otp network error: {exc}")
        raise ValueError("Failed to send OTP. Please try again.") from exc

    if resp.status_code != 200:
        logger.error(f"MSG91 send_otp error: status={resp.status_code} body={resp.text}")
        raise ValueError("Failed to send OTP. Please try again.")

    logger.info(f"OTP sent to {email} via MSG91 Email API")


async def verify_otp(email: str, otp: str) -> bool:
    """Verify an OTP for the given email against the locally stored value.

    Returns True if verified.
    Raises ValueError if verification fails.
    """
    entry = _otp_store.get(email.lower())

    if not entry:
        raise ValueError("No OTP was sent to this email. Please request a new one.")

    if time.time() > entry["expires_at"]:
        _otp_store.pop(email.lower(), None)
        raise ValueError("OTP has expired. Please request a new one.")

    if entry["otp"] != otp:
        raise ValueError("Invalid OTP. Please try again.")

    # OTP verified — remove it so it can't be reused
    _otp_store.pop(email.lower(), None)
    logger.info(f"OTP verified for {email}")
    return True
