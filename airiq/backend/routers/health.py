"""
routers/health.py — Lightweight health-check endpoints.

  GET /ping    → liveness probe (always 200 if the process is alive)
  GET /health  → readiness probe (checks Redis + Scheduler + per-city status)
"""

from datetime import datetime, timezone

from fastapi import APIRouter

from cache import redis_status, get_json
from scheduler import scheduler_status

router = APIRouter()


# ---------------------------------------------------------------------------
# GET /ping — simple liveness probe
# ---------------------------------------------------------------------------
@router.get("/ping", summary="Liveness probe")
async def ping():
    """Returns 200 immediately.  Use this to check the process is alive."""
    return {
        "status":    "ok",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


# ---------------------------------------------------------------------------
# GET /health — readiness / dependency probe
# ---------------------------------------------------------------------------
@router.get("/health", summary="Readiness probe with per-city status")
async def health():
    """
    Checks downstream dependencies and returns live per-city / per-station data.

    - **redis**: 'ok' if Redis is connected, 'degraded' if using in-memory fallback.
    - **scheduler**: 'running' if APScheduler is active, 'stopped' otherwise.
    - **cities**: per-city snapshot written by the scheduler each tick.
      Read from Redis key ``health:{city_id}``.
    """
    redis     = await redis_status()
    scheduler = scheduler_status()

    # ── Per-city status: read whatever the scheduler has written ─────────────
    # Import here to avoid circular imports at module load time
    from ingestion.city_runner import CITY_IDS

    cities: dict = {}
    for city_id in CITY_IDS:
        city_health = await get_json(f"health:{city_id}")
        if city_health:
            cities[city_id] = city_health
        else:
            # Scheduler hasn't run yet for this city
            cities[city_id] = {
                "last_fetch":    None,
                "station_count": 0,
                "stations":      {},
                "status":        "pending",
            }

    # Derive overall status
    overall = "ok" if redis == "ok" and scheduler == "running" else "degraded"

    return {
        "status":    overall,
        "redis":     redis,
        "scheduler": scheduler,
        "cities":    cities,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
