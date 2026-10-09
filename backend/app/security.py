"""Password hashing, session tokens and the login rate limiter."""

from __future__ import annotations

import hashlib
import hmac
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any

import bcrypt
from fastapi import Request
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from app.config import BCRYPT_MAX_PASSWORD_BYTES, Settings

SESSION_COOKIE = "vtol_session"
SESSION_SALT = "session"
SESSION_MAX_AGE_SECONDS = 30 * 24 * 3600

RATE_LIMIT_WINDOW_SECONDS = 15 * 60
RATE_LIMIT_PER_IP = 5
RATE_LIMIT_GLOBAL = 30


def password_too_long(password: str) -> bool:
    """bcrypt 5 raises on inputs over 72 *bytes*; reject them before hashing."""
    return len(password.encode("utf-8")) > BCRYPT_MAX_PASSWORD_BYTES


def hash_password(password: str) -> str:
    if password_too_long(password):
        raise ValueError("Password is longer than 72 bytes.")
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("ascii")


def verify_password(password: str, password_hash: str) -> bool:
    """Timing-safe comparison through bcrypt. Over-long input is always False."""
    if password_too_long(password):
        return False
    try:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("ascii"))
    except ValueError:
        return False


def password_fingerprint(password_hash: str) -> str:
    """First 16 hex chars of sha256(hash): stored in the cookie so a password change
    invalidates every session without storing the hash itself."""
    return hashlib.sha256(password_hash.encode("ascii")).hexdigest()[:16]


def session_fingerprint(settings: Settings) -> str:
    """The value the cookie must carry to be accepted, stable across restarts.

    With ``APP_PASSWORD_HASH`` the hash itself is fixed, so sha256(hash) is used. With
    ``APP_PASSWORD`` the bcrypt hash is re-salted on every start, so it cannot be the
    fingerprint: an HMAC of the password under the secret key is deterministic for the
    same (secret, password) pair and changes when either one changes, which is exactly
    when every device must be signed out.
    """
    if settings.app_password_hash and settings.app_password_hash.get_secret_value():
        return password_fingerprint(settings.app_password_hash.get_secret_value())
    if settings.app_password and settings.app_password.get_secret_value():
        digest = hmac.new(
            settings.secret_key.encode("utf-8"),
            settings.app_password.get_secret_value().encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        return digest[:16]
    raise RuntimeError(
        "No owner password configured. Set APP_PASSWORD (or APP_PASSWORD_HASH) in the "
        "server environment."
    )


def active_password_hash(settings: Settings) -> str:
    """The bcrypt hash the owner authenticates against, derived from the environment."""
    if settings.app_password_hash and settings.app_password_hash.get_secret_value():
        return settings.app_password_hash.get_secret_value()
    if settings.app_password and settings.app_password.get_secret_value():
        return hash_password(settings.app_password.get_secret_value())
    raise RuntimeError(
        "No owner password configured. Set APP_PASSWORD (or APP_PASSWORD_HASH) in the "
        "server environment."
    )


class SessionSigner:
    """Signs and verifies the session cookie value."""

    def __init__(self, secret_key: str) -> None:
        self._serializer = URLSafeTimedSerializer(secret_key, salt=SESSION_SALT)

    def create(self, user_id: int, fingerprint: str) -> str:
        return self._serializer.dumps({"uid": user_id, "pw": fingerprint})

    def load(self, token: str) -> dict[str, Any] | None:
        try:
            data = self._serializer.loads(token, max_age=SESSION_MAX_AGE_SECONDS)
        except (BadSignature, SignatureExpired):
            return None
        if not isinstance(data, dict) or "uid" not in data or "pw" not in data:
            return None
        return data


class LoginRateLimiter:
    """In-memory failure counter: per client IP and a global bucket. Resets on restart."""

    def __init__(
        self,
        per_ip: int = RATE_LIMIT_PER_IP,
        global_limit: int = RATE_LIMIT_GLOBAL,
        window_seconds: float = RATE_LIMIT_WINDOW_SECONDS,
    ) -> None:
        self.per_ip = per_ip
        self.global_limit = global_limit
        self.window = window_seconds
        self._by_ip: dict[str, deque[float]] = {}
        self._global: deque[float] = deque()
        self._lock = threading.Lock()

    def _prune(self, bucket: deque[float], now: float) -> None:
        cutoff = now - self.window
        while bucket and bucket[0] < cutoff:
            bucket.popleft()

    def retry_after_seconds(self, ip: str) -> float:
        """0 when the client may try; otherwise seconds until the oldest failure expires."""
        now = time.monotonic()
        with self._lock:
            self._prune(self._global, now)
            ip_bucket = self._by_ip.get(ip)
            if ip_bucket is not None:
                self._prune(ip_bucket, now)
                if not ip_bucket:
                    del self._by_ip[ip]
            waits: list[float] = []
            if ip_bucket and len(ip_bucket) >= self.per_ip:
                waits.append(ip_bucket[0] + self.window - now)
            if len(self._global) >= self.global_limit:
                waits.append(self._global[0] + self.window - now)
            return max(waits) if waits else 0.0

    def record_failure(self, ip: str) -> None:
        now = time.monotonic()
        with self._lock:
            self._sweep(now)
            self._by_ip.setdefault(ip, deque()).append(now)
            self._global.append(now)

    def _sweep(self, now: float) -> None:
        """Drop every per-IP bucket whose newest failure is outside the window, so an IP
        that failed once and never returned does not keep its bucket forever."""
        cutoff = now - self.window
        stale = [ip for ip, bucket in self._by_ip.items() if not bucket or bucket[-1] < cutoff]
        for ip in stale:
            del self._by_ip[ip]

    def record_success(self, ip: str) -> None:
        with self._lock:
            self._by_ip.pop(ip, None)

    def reset(self) -> None:
        with self._lock:
            self._by_ip.clear()
            self._global.clear()


def client_ip(request: Request, settings: Settings) -> str:
    """Fly's own view of the client in production; the socket peer otherwise.

    ``X-Forwarded-For`` is never consulted: its leftmost entries are client-supplied.
    """
    if settings.is_production:
        fly_ip = request.headers.get("fly-client-ip")
        if fly_ip:
            return fly_ip.strip()
    return request.client.host if request.client else "unknown"


@dataclass
class AuthState:
    """Per-process authentication state, built at startup and stored on ``app.state.auth``."""

    password_hash: str
    fingerprint: str
    signer: SessionSigner
    rate_limiter: LoginRateLimiter = field(default_factory=LoginRateLimiter)

    @classmethod
    def from_settings(cls, settings: Settings) -> AuthState:
        password_hash = active_password_hash(settings)
        return cls(
            password_hash=password_hash,
            fingerprint=session_fingerprint(settings),
            signer=SessionSigner(settings.secret_key),
        )
