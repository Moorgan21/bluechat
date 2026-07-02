"""
Token-bucket rate limiter for Gemini API.
Limit is read from GEMINI_RPM env var (default: 100 requests per minute).
"""

import asyncio
import os
import time

_RPM = int(os.environ.get("GEMINI_RPM", "100"))


class _TokenBucket:
    def __init__(self, rate: float, capacity: float):
        self._rate = rate          # tokens per second
        self._capacity = capacity
        self._tokens = capacity
        self._last = time.monotonic()
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        while True:
            async with self._lock:
                now = time.monotonic()
                self._tokens = min(
                    self._capacity,
                    self._tokens + (now - self._last) * self._rate,
                )
                self._last = now
                if self._tokens >= 1:
                    self._tokens -= 1
                    return
                wait = (1 - self._tokens) / self._rate
            await asyncio.sleep(wait)


gemini_limiter = _TokenBucket(rate=_RPM / 60, capacity=_RPM)
