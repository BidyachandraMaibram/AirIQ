"""
cache.py — Redis client singleton with in-memory fallback.

Public API (all async):
  init_redis()              → called on startup
  close_redis()             → called on shutdown
  get_json(key)             → return parsed object or None
  set_json(key, val, ttl)   → store JSON-serialised value with optional TTL
"""

import json
import logging
from typing import Any

import redis.asyncio as aioredis

from config import settings

logger = logging.getLogger("airiq.cache")

# ---------------------------------------------------------------------------
# Module-level state
# ---------------------------------------------------------------------------
_redis_client: aioredis.Redis | None = None   # real Redis connection
_memory_store: dict[str, str] = {}            # fallback when Redis is down
_use_memory_fallback: bool = False


# ---------------------------------------------------------------------------
# Lifecycle helpers (called from main.py lifespan)
# ---------------------------------------------------------------------------
async def init_redis() -> None:
    """Try to connect to Redis.  If unreachable, enable in-memory fallback."""
    global _redis_client, _use_memory_fallback

    try:
        client = aioredis.from_url(
            settings.redis_url,
            encoding="utf-8",
            decode_responses=True,
            socket_connect_timeout=2,   # fail fast if Redis is down
        )
        await client.ping()             # confirm connection
        _redis_client = client
        _use_memory_fallback = False
        logger.info("Redis connected → %s", settings.redis_url)
    except Exception as exc:
        # Non-fatal: switch to in-memory dict so the app still starts
        logger.warning(
            "Redis unreachable (%s). Using in-memory cache — "
            "data will NOT persist across restarts.",
            exc,
        )
        _redis_client = None
        _use_memory_fallback = True


async def close_redis() -> None:
    """Gracefully close the Redis connection pool."""
    global _redis_client
    if _redis_client is not None:
        await _redis_client.aclose()
        _redis_client = None
        logger.info("Redis connection closed.")


# ---------------------------------------------------------------------------
# Public helper functions
# ---------------------------------------------------------------------------
async def get_json(key: str) -> Any | None:
    """Fetch and JSON-decode a cached value.  Returns None on miss."""
    if _use_memory_fallback:
        raw = _memory_store.get(key)
    else:
        raw = await _redis_client.get(key)  # type: ignore[union-attr]

    if raw is None:
        return None

    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        logger.warning("Cache hit for key '%s' but value is not valid JSON.", key)
        return None


async def set_json(key: str, value: Any, ttl_seconds: int | None = None) -> None:
    """JSON-encode and store a value.  Optionally set TTL (ignored by in-memory)."""
    serialised = json.dumps(value, default=str)

    if _use_memory_fallback:
        # In-memory store has no TTL support — simple key/value
        _memory_store[key] = serialised
    else:
        if ttl_seconds:
            await _redis_client.setex(key, ttl_seconds, serialised)  # type: ignore[union-attr]
        else:
            await _redis_client.set(key, serialised)  # type: ignore[union-attr]


# ---------------------------------------------------------------------------
# Status helper (used by /health endpoint)
# ---------------------------------------------------------------------------
async def redis_status() -> str:
    """Return 'ok' if Redis is connected, 'degraded' if using fallback."""
    if _use_memory_fallback:
        return "degraded"
    try:
        await _redis_client.ping()  # type: ignore[union-attr]
        return "ok"
    except Exception:
        return "degraded"
