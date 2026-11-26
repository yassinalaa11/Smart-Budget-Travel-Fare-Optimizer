from __future__ import annotations

import asyncio
import random
from typing import Any

import httpx

RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}


def _retry_delay(
    attempt: int,
    *,
    base_delay: float,
    max_delay: float,
    retry_after: str | None = None,
) -> float:
    if retry_after:
        try:
            parsed = float(retry_after)
            if parsed > 0:
                return min(parsed, max_delay) + random.uniform(0.0, 0.5)
        except ValueError:
            pass
    exp_delay = min(base_delay * (2 ** attempt), max_delay)
    return random.uniform(exp_delay * 0.5, exp_delay)


async def request_with_retry(
    client: httpx.AsyncClient,
    method: str,
    url: str,
    *,
    max_retries: int = 4,
    base_delay: float = 1.0,
    max_delay: float = 30.0,
    **kwargs: Any,
) -> httpx.Response:
    last_exc: Exception | None = None
    for attempt in range(max_retries + 1):
        try:
            response = await client.request(method, url, **kwargs)
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            last_exc = exc
            if attempt >= max_retries:
                raise
            await asyncio.sleep(_retry_delay(attempt, base_delay=base_delay, max_delay=max_delay))
            continue

        if response.status_code not in RETRYABLE_STATUS_CODES or attempt >= max_retries:
            return response

        delay = _retry_delay(
            attempt,
            base_delay=base_delay,
            max_delay=max_delay,
            retry_after=response.headers.get("Retry-After"),
        )
        await asyncio.sleep(delay)

    if last_exc is not None:
        raise last_exc
    raise RuntimeError("Unexpected retry termination")
