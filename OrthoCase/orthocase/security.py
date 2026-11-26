"""Dashboard authentication: one clinic password, a signed session cookie,
and a required custom header on state-changing requests (CSRF defence)."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time

from fastapi import HTTPException, Request

COOKIE_NAME = "orthocase_session"
SESSION_SECONDS = 12 * 3600
CSRF_HEADER = "x-requested-with"
CSRF_VALUE = "orthocase"

_LOGIN_WINDOW = 600
_LOGIN_MAX = 8
_login_attempts: dict[str, list[float]] = {}


def _sign(secret: bytes, payload: bytes) -> str:
    return hmac.new(secret, payload, hashlib.sha256).hexdigest()


def make_session(secret: bytes, now: float | None = None) -> str:
    payload = base64.urlsafe_b64encode(
        json.dumps({"exp": int((now or time.time()) + SESSION_SECONDS)}).encode()
    )
    return f"{payload.decode()}.{_sign(secret, payload)}"


def valid_session(secret: bytes, cookie: str | None, now: float | None = None) -> bool:
    if not cookie or "." not in cookie:
        return False
    payload, signature = cookie.rsplit(".", 1)
    if not hmac.compare_digest(_sign(secret, payload.encode()), signature):
        return False
    try:
        data = json.loads(base64.urlsafe_b64decode(payload.encode()))
    except (ValueError, json.JSONDecodeError):
        return False
    return float(data.get("exp", 0)) > (now or time.time())


def check_password(expected: str, given: str) -> bool:
    return hmac.compare_digest(expected.encode(), given.encode())


def login_allowed(client: str, now: float | None = None) -> bool:
    now = now or time.time()
    attempts = [t for t in _login_attempts.get(client, []) if now - t < _LOGIN_WINDOW]
    _login_attempts[client] = attempts
    return len(attempts) < _LOGIN_MAX


def record_failed_login(client: str, now: float | None = None) -> None:
    _login_attempts.setdefault(client, []).append(now or time.time())


def reset_login_attempts() -> None:
    _login_attempts.clear()


def is_authenticated(request: Request) -> bool:
    secret = request.app.state.config.secret_key
    return valid_session(secret, request.cookies.get(COOKIE_NAME))


def require_auth(request: Request) -> None:
    if not is_authenticated(request):
        raise HTTPException(status_code=401, detail="Sign in to continue.")
    if request.method not in ("GET", "HEAD", "OPTIONS"):
        if request.headers.get(CSRF_HEADER, "").lower() != CSRF_VALUE:
            raise HTTPException(status_code=403, detail="Missing request header.")


class RateLimiter:
    """Small in-memory limiter for the public plan endpoints."""

    def __init__(self, limit: int, window: float):
        self.limit = limit
        self.window = window
        self.hits: dict[str, list[float]] = {}

    def allow(self, key: str, now: float | None = None) -> bool:
        now = now or time.time()
        hits = [t for t in self.hits.get(key, []) if now - t < self.window]
        if len(hits) >= self.limit:
            self.hits[key] = hits
            return False
        hits.append(now)
        self.hits[key] = hits
        return True
