"""Async request pacing and quota tracking for external providers."""

from __future__ import annotations

import asyncio
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import AsyncIterator


class QuotaExceeded(RuntimeError):
    """Raised before a request when a provider quota has been exhausted."""


@dataclass(frozen=True)
class ProviderLimit:
    requests_per_second: float
    monthly_quota: int | None = None
    daily_quota: int | None = None
    max_concurrency: int = 1


class TokenBucketLimiter:
    """A process-local token bucket with UTC daily and monthly quota counters."""

    def __init__(self, limits: ProviderLimit) -> None:
        if limits.requests_per_second <= 0:
            raise ValueError("requests_per_second must be positive")
        if limits.max_concurrency < 1:
            raise ValueError("max_concurrency must be at least 1")
        self.limits = limits
        self._tokens = 1.0
        self._last_refill = time.monotonic()
        self._lock = asyncio.Lock()
        self._semaphore = asyncio.Semaphore(limits.max_concurrency)
        self._day_key: str | None = None
        self._month_key: str | None = None
        self._daily_count = 0
        self._monthly_count = 0

    @asynccontextmanager
    async def request_slot(self) -> AsyncIterator[None]:
        """Wait for pacing/quota availability and hold one concurrency slot."""
        async with self._semaphore:
            while True:
                wait_seconds = 0.0
                async with self._lock:
                    now_utc = datetime.now(timezone.utc)
                    day_key = now_utc.date().isoformat()
                    month_key = now_utc.strftime("%Y-%m")
                    if day_key != self._day_key:
                        self._day_key, self._daily_count = day_key, 0
                    if month_key != self._month_key:
                        self._month_key, self._monthly_count = month_key, 0

                    if (
                        self.limits.daily_quota is not None
                        and self._daily_count >= self.limits.daily_quota
                    ):
                        raise QuotaExceeded("provider daily request quota exhausted")
                    if (
                        self.limits.monthly_quota is not None
                        and self._monthly_count >= self.limits.monthly_quota
                    ):
                        raise QuotaExceeded("provider monthly request quota exhausted")

                    loop_now = time.monotonic()
                    elapsed = loop_now - self._last_refill
                    self._tokens = min(
                        1.0,
                        self._tokens + elapsed * self.limits.requests_per_second,
                    )
                    self._last_refill = loop_now
                    if self._tokens >= 1.0:
                        self._tokens -= 1.0
                        self._daily_count += 1
                        self._monthly_count += 1
                        break
                    wait_seconds = (1.0 - self._tokens) / self.limits.requests_per_second

                await asyncio.sleep(wait_seconds)

            yield
