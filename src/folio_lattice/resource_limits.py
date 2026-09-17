from __future__ import annotations

import math
import threading
import time
from collections.abc import Callable, Mapping


class BoundedRateLimiter:
    def __init__(
        self,
        *,
        limit: int,
        window_seconds: float = 60.0,
        max_keys: int = 4096,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if limit < 1 or window_seconds <= 0 or max_keys < 1:
            raise ValueError("rate limiter bounds must be positive")
        self.limit = limit
        self.window_seconds = window_seconds
        self.max_keys = max_keys
        self._clock = clock
        self._lock = threading.Lock()
        self._windows: dict[str, tuple[float, int]] = {}

    def allow(self, key: str) -> tuple[bool, int]:
        now = self._clock()
        with self._lock:
            expired = [
                candidate
                for candidate, (started_at, _count) in self._windows.items()
                if now - started_at >= self.window_seconds
            ]
            for candidate in expired:
                del self._windows[candidate]
            window = self._windows.get(key)
            if window is None:
                if len(self._windows) >= self.max_keys:
                    return False, max(1, math.ceil(self.window_seconds))
                self._windows[key] = (now, 1)
                return True, 0
            started_at, count = window
            if count >= self.limit:
                return False, max(1, math.ceil(self.window_seconds - (now - started_at)))
            self._windows[key] = (started_at, count + 1)
            return True, 0


class DimensionRateLimiter:
    """Atomically charge tenant/actor/IP buckets for one request."""

    def __init__(
        self,
        *,
        limits: Mapping[str, int],
        window_seconds: float = 60.0,
        max_keys: int = 4096,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if not limits or any(limit < 1 for limit in limits.values()):
            raise ValueError("dimension rate limits must be positive")
        if window_seconds <= 0 or max_keys < 1:
            raise ValueError("dimension rate bounds must be positive")
        self.limits = dict(limits)
        self.window_seconds = window_seconds
        self.max_keys = max_keys
        self._clock = clock
        self._lock = threading.Lock()
        self._windows: dict[tuple[str, str], tuple[float, int]] = {}

    def allow(self, dimensions: Mapping[str, str]) -> tuple[bool, int]:
        now = self._clock()
        candidates = [
            (name, value) for name, value in dimensions.items() if name in self.limits and value
        ]
        if not candidates:
            return False, max(1, math.ceil(self.window_seconds))
        with self._lock:
            expired = [
                candidate
                for candidate, (started_at, _count) in self._windows.items()
                if now - started_at >= self.window_seconds
            ]
            for candidate in expired:
                del self._windows[candidate]
            for name, value in candidates:
                window = self._windows.get((name, value))
                if window is not None:
                    started_at, count = window
                    if count >= self.limits[name]:
                        return False, max(1, math.ceil(self.window_seconds - (now - started_at)))
                elif (
                    sum(candidate_name == name for candidate_name, _ in self._windows)
                    >= self.max_keys
                ):
                    return False, max(1, math.ceil(self.window_seconds))
            for name, value in candidates:
                started_at, count = self._windows.get((name, value), (now, 0))
                self._windows[(name, value)] = (started_at, count + 1)
            return True, 0


class BoundedConcurrencyLimiter:
    def __init__(self, *, limit: int, per_key_limit: int, max_keys: int) -> None:
        if limit < 1 or per_key_limit < 1 or max_keys < 1:
            raise ValueError("concurrency bounds must be positive")
        self.limit = limit
        self.per_key_limit = per_key_limit
        self.max_keys = max_keys
        self._active: dict[str, int] = {}
        self._total = 0
        self._lock = threading.Lock()

    def try_acquire(self, key: str) -> bool:
        with self._lock:
            active = self._active.get(key, 0)
            if (
                self._total >= self.limit
                or active >= self.per_key_limit
                or (active == 0 and len(self._active) >= self.max_keys)
            ):
                return False
            self._active[key] = active + 1
            self._total += 1
            return True

    def release(self, key: str) -> None:
        with self._lock:
            active = self._active.get(key, 0)
            if active == 0:
                return
            if active <= 1:
                self._active.pop(key, None)
            else:
                self._active[key] = active - 1
            if self._total > 0:
                self._total -= 1
