import os
from contextvars import ContextVar

from fastapi import Request
from slowapi import Limiter
from slowapi.util import get_remote_address
from starlette.middleware.base import BaseHTTPMiddleware

_WHITELISTED_IPS = {
    ip.strip()
    for ip in os.getenv("RATE_LIMIT_WHITELIST", "").split(",")
    if ip.strip()
}

_request_ctx: ContextVar[Request] = ContextVar("request_ctx")


def _is_whitelisted() -> bool:
    """Zero-arg callable for slowapi 0.1.9 exempt_when — reads request from ContextVar."""
    try:
        request = _request_ctx.get()
        return get_remote_address(request) in _WHITELISTED_IPS
    except LookupError:
        return False


class RequestContextMiddleware(BaseHTTPMiddleware):
    """Stores the current request in a ContextVar so _is_whitelisted() can read it."""

    async def dispatch(self, request: Request, call_next):
        _request_ctx.set(request)
        return await call_next(request)


limiter = Limiter(key_func=get_remote_address)
