"""Per-tenant rate limiting via a Redis sliding-window counter.

Architecture §6.2: keyed by company_id, enforced in middleware *before* the
router. Exceeding the limit returns HTTP 429 + Retry-After. The free tier is
100 analyses/day per PRD §6.1; we expose a per-minute RPM window here for
burst protection and a separate monthly_doc_limit quota in the handler.

Tests inject a fakeredis client via `get_redis` so they don't need a live
Redis — and so a Redis outage degrades to "allow" rather than taking the API
down (rate limiting is a protection, not a hard dependency at MVP).
"""
from __future__ import annotations

import time
from typing import Any

import redis.asyncio as redis

from app.config import get_settings

_redis: redis.Redis | None = None


async def get_redis() -> redis.Redis:
    """Singleton async Redis client. Tests override this via module patching."""
    global _redis
    if _redis is None:
        _redis = redis.from_url(
            get_settings().redis_url,
            encoding="utf-8",
            decode_responses=True,
        )
    return _redis


async def check_rate_limit(
    company_id: str,
    redis_client: Any | None = None,
    limit_per_minute: int | None = None,
) -> int:
    """Increment the per-minute window for this tenant.

    Returns the seconds until the caller may retry if over the limit; returns 0
    if the request is allowed. Raises nothing — the caller decides how to map
    the `retry_after` value to an HTTP response.
    """
    settings = get_settings()
    limit = limit_per_minute or settings.rate_limit_rpm
    client = redis_client or await get_redis()

    now = time.time()
    window_start = now - 60.0
    key = f"ratelimit:{company_id}"

    # Sliding window via ZSET: drop entries older than 60s, count current.
    pipe = client.pipeline()
    pipe.zremrangebyscore(key, 0, window_start)
    pipe.zadd(key, {f"{now:.6f}": now})
    pipe.zcard(key)
    pipe.expire(key, 120)
    try:
        results = await pipe.execute()
    except Exception:
        # Redis unavailable — degrade to "allow". Rate limiting protects the
        # service; a hard dependency on Redis would make it *less* available.
        return 0

    count = int(results[2])
    if count > limit:
        retry_after = max(1, int(60 - (now - window_start)))
        return retry_after
    return 0
