"""API-key authentication, role checks and a small in-memory rate limiter."""

from __future__ import annotations

import hashlib
import hmac
import threading
import time
from collections import deque
from dataclasses import dataclass

from app.core.config import ApiKeyConfig
from app.core.exceptions import AuthenticationError, AuthorizationError, RateLimitExceededError
from app.domain.enums import Role


@dataclass(frozen=True)
class Principal:
    name: str
    role: Role
    company_code: str

    def require(self, role: Role) -> None:
        if self.role.rank < role.rank:
            raise AuthorizationError(f"This action requires the {role.value} role")


def hash_api_key(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


class ApiKeyAuthenticator:
    def __init__(self, keys: list[ApiKeyConfig]) -> None:
        self._keys = keys

    def authenticate(self, presented: str | None) -> Principal:
        if not presented:
            raise AuthenticationError("Missing X-API-Key header")
        digest = hash_api_key(presented)
        match = None
        for cfg in self._keys:  # compare against every key: constant work regardless of position
            if hmac.compare_digest(digest, cfg.key_sha256.lower()):
                match = cfg
        if match is None:
            raise AuthenticationError("Invalid API key")
        return Principal(name=match.name, role=match.role, company_code=match.company_code)


class RateLimiter:
    """Sliding one-minute window per principal. Suitable for a single process; use a shared store when scaling."""

    def __init__(self, per_minute: int) -> None:
        self.per_minute = per_minute
        self._hits: dict[str, deque[float]] = {}
        self._lock = threading.Lock()

    def check(self, key: str) -> None:
        now = time.monotonic()
        with self._lock:
            hits = self._hits.setdefault(key, deque())
            while hits and now - hits[0] > 60:
                hits.popleft()
            if len(hits) >= self.per_minute:
                raise RateLimitExceededError("Rate limit exceeded; retry later")
            hits.append(now)
