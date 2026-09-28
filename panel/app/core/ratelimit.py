"""Small dependency-free rate limiter.

Two layers:
  * `SlidingWindowLimiter` - in-process, used for cheap API throttling.
  * DB-backed counters (see `services.audit`) for persistent login lockout,
    which survives restarts and works across multiple Render instances.

For a multi-instance deployment swap the in-process limiter for Redis; the
interface is intentionally identical.
"""
from __future__ import annotations

import threading
import time
from collections import defaultdict, deque
from typing import Deque, Dict, Tuple


class SlidingWindowLimiter:
    def __init__(self, max_events: int, window_seconds: int) -> None:
        self.max_events = max_events
        self.window = window_seconds
        self._hits: Dict[str, Deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def check(self, key: str) -> Tuple[bool, int]:
        """Return (allowed, retry_after_seconds)."""
        now = time.monotonic()
        cutoff = now - self.window
        with self._lock:
            bucket = self._hits[key]
            while bucket and bucket[0] < cutoff:
                bucket.popleft()
            if len(bucket) >= self.max_events:
                retry = int(bucket[0] + self.window - now) + 1
                return False, max(retry, 1)
            bucket.append(now)
            return True, 0

    def reset(self, key: str) -> None:
        with self._lock:
            self._hits.pop(key, None)

    def prune(self) -> None:
        now = time.monotonic()
        cutoff = now - self.window
        with self._lock:
            for key in list(self._hits):
                bucket = self._hits[key]
                while bucket and bucket[0] < cutoff:
                    bucket.popleft()
                if not bucket:
                    self._hits.pop(key, None)


class TokenBucket:
    """Smooths bursts; used for outbound calls to Telegram / node agents."""

    def __init__(self, rate_per_second: float, burst: int) -> None:
        self.rate = rate_per_second
        self.burst = burst
        self._tokens: Dict[str, float] = defaultdict(lambda: float(burst))
        self._last: Dict[str, float] = defaultdict(time.monotonic)
        self._lock = threading.Lock()

    def allow(self, key: str = "default", cost: float = 1.0) -> bool:
        now = time.monotonic()
        with self._lock:
            elapsed = now - self._last[key]
            self._last[key] = now
            self._tokens[key] = min(self.burst, self._tokens[key] + elapsed * self.rate)
            if self._tokens[key] >= cost:
                self._tokens[key] -= cost
                return True
            return False
