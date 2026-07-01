"""
backend/ingestion/city_runner.py
----------------------------------
Orchestrates one full ingestion cycle for a single city:
  1. Fetch AQI from CPCB (real or mock)
  2. Run quality checks on each record
  3. Fetch weather from Open-Meteo
  4. Cache everything in Redis
  5. Write a health snapshot to Redis (key: health:{city_id})

Called by the APScheduler tick in scheduler.py.
"""

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from cache import get_json, set_json
from ingestion.cpcb_fetcher import fetch_city_aqi, mock_city_aqi
from ingestion.weather_fetcher import fetch_city_weather
from ingestion.quality_checker import check_quality_batch

logger = logging.getLogger("airiq.city_runner")

# ---------------------------------------------------------------------------
# City registry — load all JSON configs from configs/
# ---------------------------------------------------------------------------
_CONFIGS_DIR = Path(__file__).resolve().parents[2] / "configs"

def _load_city_configs() -> dict[str, dict]:
    """Load every *.json file in configs/ and index by city_id."""
    configs: dict[str, dict] = {}
    for path in sorted(_CONFIGS_DIR.glob("*.json")):
        try:
            with open(path, encoding="utf-8") as f:
                cfg = json.load(f)
            city_id = cfg.get("city_id")
            if city_id:
                configs[city_id] = cfg
        except Exception as exc:
            logger.warning("Could not load config %s: %s", path.name, exc)
    return configs

CITY_CONFIGS: dict[str, dict] = _load_city_configs()
CITY_IDS: list[str]           = list(CITY_CONFIGS.keys())

logger.info("Loaded city configs: %s", CITY_IDS)


# ---------------------------------------------------------------------------
# TTLs (seconds)
# ---------------------------------------------------------------------------
AQI_TTL     = 60 * 35    # 35 minutes — slightly longer than poll interval
WEATHER_TTL = 60 * 60    # 1 hour
HEALTH_TTL  = 60 * 35    # same as AQI


# ---------------------------------------------------------------------------
# Single-city ingestion cycle
# ---------------------------------------------------------------------------
async def run_city_cycle(city_config: dict) -> None:
    """
    Full ingestion + caching cycle for one city.
    Never raises — all errors are logged.
    """
    city_id = city_config["city_id"]

    try:
        # ── 1. Fetch AQI ─────────────────────────────────────────────────────
        records = await fetch_city_aqi(city_config)

        # Fall back to mock if the live fetch returned nothing
        if not records:
            logger.warning("[%s] Live fetch empty — using mock data.", city_id)
            records = mock_city_aqi(city_config)

        # ── 2. Quality check ─────────────────────────────────────────────────
        checked = check_quality_batch(records)

        # ── 3. Cache AQI records ─────────────────────────────────────────────
        await set_json(f"aqi:{city_id}", checked, ttl_seconds=AQI_TTL)

        # Also cache individual station records for fast lookups
        for rec in checked:
            sid = rec.get("station_id", "unknown")
            await set_json(f"aqi:{city_id}:{sid}", rec, ttl_seconds=AQI_TTL)

        # ── 4. Fetch weather ─────────────────────────────────────────────────
        weather = await fetch_city_weather(city_config)
        await set_json(f"weather:{city_id}", weather, ttl_seconds=WEATHER_TTL)

        # ── 5. Write health snapshot ─────────────────────────────────────────
        stations_health: dict[str, dict] = {}
        for rec in checked:
            sid = rec.get("station_id", "unknown")
            stations_health[sid] = {
                "aqi":           rec.get("aqi"),
                "quality_score": rec.get("quality_score", 0.0),
                "flags":         rec.get("quality_flags", []),
                "timestamp":     rec.get("timestamp"),
            }

        health_blob = {
            "last_fetch":    datetime.now(timezone.utc).isoformat(),
            "station_count": len(checked),
            "stations":      stations_health,
            "weather_ok":    not weather.get("_fallback", False),
        }
        await set_json(f"health:{city_id}", health_blob, ttl_seconds=HEALTH_TTL)

        logger.info(
            "[%s] cycle complete — %d stations, weather_ok=%s",
            city_id, len(checked), health_blob["weather_ok"],
        )

    except Exception as exc:
        # Safety net — log but never crash the scheduler
        logger.error("[%s] Unhandled error in city cycle: %s", city_id, exc, exc_info=True)


# ---------------------------------------------------------------------------
# All-city runner (called by scheduler tick)
# ---------------------------------------------------------------------------
async def run_all_cities() -> None:
    """Run ingestion for every configured city sequentially."""
    logger.info("=== Starting ingestion cycle for %d cities ===", len(CITY_CONFIGS))
    for city_id, cfg in CITY_CONFIGS.items():
        await run_city_cycle(cfg)
    logger.info("=== Ingestion cycle complete ===")
