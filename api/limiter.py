import os

from fastapi import Request
from slowapi import Limiter
from slowapi.util import get_remote_address

_WHITELISTED_IPS = {
    ip.strip()
    for ip in os.getenv("RATE_LIMIT_WHITELIST", "").split(",")
    if ip.strip()
}


def _is_whitelisted(request: Request) -> bool:
    return get_remote_address(request) in _WHITELISTED_IPS


limiter = Limiter(key_func=get_remote_address, default_limits_exempt_when=_is_whitelisted)
