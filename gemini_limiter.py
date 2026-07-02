"""
Token-bucket rate limiter for Gemini API — 100 requests per minute.
"""

import asyncio
import time


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


# 100 req/min = 100/60 tokens per second, burst up to 100
gemini_limiter = _TokenBucket(rate=100 / 60, capacity=100)
