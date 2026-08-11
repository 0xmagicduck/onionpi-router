from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import time
from collections import defaultdict, deque
from collections.abc import Iterator
from contextlib import contextmanager
from threading import BoundedSemaphore, Lock

#: Cost written by hash_password, and the floor verify_password accepts. The
#: parameters travel inside each digest so they can be raised later without
#: invalidating existing accounts; they are never allowed to go down.
SCRYPT_N = 2**14
SCRYPT_R = 8
SCRYPT_P = 1
SCRYPT_DKLEN = 32
#: Above this, one verification would allocate more memory than the Pi has.
SCRYPT_MAX_N = 2**20

#: Verifications allowed to run at the same time, process wide. One of them
#: holds SCRYPT_N * SCRYPT_R * 128 bytes — 16 MiB with the parameters above —
#: and FastAPI answers each login in its own worker thread. The login limiter
#: cannot bound this on its own: it only counts attempts that already finished,
#: so a burst of simultaneous requests from a single Wi-Fi client would all pass
#: it and hash at once, which is enough to exhaust a 1 GiB Raspberry Pi.
HASHING_SLOTS = 4
#: Waiting costs a parked thread and no memory, so a real operator queues
#: behind a burst instead of being refused.
HASHING_WAIT_SECONDS = 10.0
_hashing_slots = BoundedSemaphore(HASHING_SLOTS)


class PasswordHashingBusy(RuntimeError):
    """Too many password verifications already in flight."""


@contextmanager
def hashing_slot(timeout: float = HASHING_WAIT_SECONDS) -> Iterator[None]:
    """Caps how much scrypt memory the whole process can hold at once."""
    if not _hashing_slots.acquire(timeout=timeout):
        raise PasswordHashingBusy("Trop de vérifications simultanées")
    try:
        yield
    finally:
        _hashing_slots.release()


def hash_password(password: str) -> str:
    if len(password) < 12:
        raise ValueError("Le mot de passe doit contenir au moins 12 caractères")
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(
        password.encode(), salt=salt, n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P, dklen=SCRYPT_DKLEN
    )
    return (
        f"scrypt${SCRYPT_N}${SCRYPT_R}${SCRYPT_P}$"
        f"{base64.urlsafe_b64encode(salt).decode()}$"
        f"{base64.urlsafe_b64encode(digest).decode()}"
    )


def verify_password(password: str, encoded: str) -> bool:
    try:
        algorithm, n, r, p, salt_value, digest_value = encoded.split("$")
        if algorithm != "scrypt":
            return False
        cost, block, parallel = int(n), int(r), int(p)
        expected = base64.urlsafe_b64decode(digest_value)
        # A digest weaker than what this version writes is refused rather than
        # verified cheaply: whoever downgraded it already had write access to
        # the database, and must not also get an inexpensive offline attack.
        if not SCRYPT_N <= cost <= SCRYPT_MAX_N:
            return False
        if block < SCRYPT_R or parallel < SCRYPT_P or len(expected) < SCRYPT_DKLEN:
            return False
        salt = base64.urlsafe_b64decode(salt_value)
        candidate = hashlib.scrypt(
            password.encode(), salt=salt, n=cost, r=block, p=parallel, dklen=len(expected)
        )
        return hmac.compare_digest(candidate, expected)
    except (ValueError, TypeError, MemoryError):
        return False


def dummy_verify() -> None:
    """Burns the same work as a real check, for a user that does not exist.

    Without it, an unknown name answers in a millisecond and a known one in the
    time scrypt takes, which is a free account enumeration oracle on a page that
    is otherwise deliberately vague about why a login failed.
    """
    hashlib.scrypt(
        b"onionpi-absent-account",
        salt=b"onionpi-absent-salt",
        n=SCRYPT_N,
        r=SCRYPT_R,
        p=SCRYPT_P,
        dklen=SCRYPT_DKLEN,
    )


def token_hash(token: str, secret: str) -> str:
    return hmac.new(secret.encode(), token.encode(), hashlib.sha256).hexdigest()


class LoginLimiter:
    """Small in-memory limiter; prevents cheap LAN password spraying.

    Two budgets, because the per-address one is only as trustworthy as the
    address. nginx rewrites the forwarded address from the real peer, so a
    Wi-Fi client cannot claim someone else's. A request that reaches the
    application without passing through nginx — the onion service, when it is
    published — carries headers its author chose, and could otherwise mint a
    fresh budget per attempt. The global budget is the ceiling that holds
    whatever the address says.
    """

    def __init__(
        self, attempts: int = 8, window_seconds: int = 300, global_attempts: int = 120
    ) -> None:
        self.attempts = attempts
        self.window_seconds = window_seconds
        self.global_attempts = global_attempts
        self._events: dict[str, deque[float]] = defaultdict(deque)
        self._global: deque[float] = deque()
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
            while self._global and now - self._global[0] > self.window_seconds:
                self._global.popleft()
            if len(self._global) >= self.global_attempts:
                return False
            return len(self._events.get(address, ())) < self.attempts

    def failure(self, address: str) -> None:
        now = time.monotonic()
        with self._lock:
            self._events[address].append(now)
            self._global.append(now)

    def success(self, address: str) -> None:
        with self._lock:
            self._events.pop(address, None)


class RateLimiter:
    """Sliding window, shared by chat connections and the speed test."""

    def __init__(self, events: int, window_seconds: float) -> None:
        self.events = events
        self.window_seconds = window_seconds
        self._stamps: deque[float] = deque()
        # The speed-test limiter is a module-level singleton reached from every
        # worker thread: without the lock, two requests can both read a
        # not-yet-full window and both be allowed.
        self._lock = Lock()

    def allow(self) -> bool:
        now = time.monotonic()
        with self._lock:
            while self._stamps and now - self._stamps[0] > self.window_seconds:
                self._stamps.popleft()
            if len(self._stamps) >= self.events:
                return False
            self._stamps.append(now)
            return True

