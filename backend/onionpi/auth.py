from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import time
from collections import defaultdict, deque
from threading import Lock


def hash_password(password: str) -> str:
    if len(password) < 12:
        raise ValueError("Le mot de passe doit contenir au moins 12 caractères")
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=2**14, r=8, p=1, dklen=32)
    return f"scrypt$16384$8$1${base64.urlsafe_b64encode(salt).decode()}${base64.urlsafe_b64encode(digest).decode()}"


def verify_password(password: str, encoded: str) -> bool:
    try:
        algorithm, n, r, p, salt_value, digest_value = encoded.split("$")
        if algorithm != "scrypt":
            return False
        salt = base64.urlsafe_b64decode(salt_value)
        expected = base64.urlsafe_b64decode(digest_value)
        candidate = hashlib.scrypt(
            password.encode(), salt=salt, n=int(n), r=int(r), p=int(p), dklen=len(expected)
        )
        return hmac.compare_digest(candidate, expected)
    except (ValueError, TypeError):
        return False


def token_hash(token: str, secret: str) -> str:
    return hmac.new(secret.encode(), token.encode(), hashlib.sha256).hexdigest()


class LoginLimiter:
    """Small in-memory limiter; prevents cheap LAN password spraying."""

    def __init__(self, attempts: int = 8, window_seconds: int = 300) -> None:
        self.attempts = attempts
        self.window_seconds = window_seconds
        self._events: dict[str, deque[float]] = defaultdict(deque)
        self._lock = Lock()

    def _prune(self, now: float) -> None:
        # Called under the lock. Keeps a long-running daemon from holding one
        # deque per address ever seen.
        for address in [key for key, events in self._events.items() if not events]:
            del self._events[address]
        for address, events in list(self._events.items()):
            while events and now - events[0] > self.window_seconds:
                events.popleft()
            if not events:
                del self._events[address]

    def allow(self, address: str) -> bool:
        now = time.monotonic()
        with self._lock:
            self._prune(now)
            return len(self._events.get(address, ())) < self.attempts

    def failure(self, address: str) -> None:
        with self._lock:
            self._events[address].append(time.monotonic())

    def success(self, address: str) -> None:
        with self._lock:
            self._events.pop(address, None)


class RateLimiter:
    """Sliding window used for chat messages on a single connection."""

    def __init__(self, events: int, window_seconds: float) -> None:
        self.events = events
        self.window_seconds = window_seconds
        self._stamps: deque[float] = deque()

    def allow(self) -> bool:
        now = time.monotonic()
        while self._stamps and now - self._stamps[0] > self.window_seconds:
            self._stamps.popleft()
        if len(self._stamps) >= self.events:
            return False
        self._stamps.append(now)
        return True

