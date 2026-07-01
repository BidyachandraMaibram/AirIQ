"""
scheduler.py — APScheduler wired to FastAPI lifespan.

Runs the complete background intelligence pipeline every 30 minutes, pre-computing
data quality, meteorology, source attribution, enforcement, forecasts, citizen advisories,
and incident command reports for every monitored ward. All results are written directly
to Redis to maintain a < 10ms query API response target.
"""

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any
from geopy.distance import geodesic

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from config import settings
from cache import set_json

logger = logging.getLogger("airiq.scheduler")

# Module-level scheduler instance shared across the app.
_scheduler = AsyncIOScheduler()


# ---------------------------------------------------------------------------
# Dynamic Ingestion + Agent Pipeline
# ---------------------------------------------------------------------------

async def run_city_pipeline(city_config: dict, redis_client: Any = None):
    """
    Runs the full agent pipeline for one city.
    
    Steps:
      1. Fetch AQI data (CPCB live API or mock fallback) + check quality
      2. Fetch weather forecast (Open-Meteo API)
      3. Cache city-wide health parameters to Redis
      4. For each ward:
         - Find closest AQI record by geodesic distance
         - Run attribution, enforcement, forecast, advisory and incident agents
         - Cache full analysis blob
      5. Cache aggregate summary records for cities
    """
    city_id = city_config.get("city_id")
    city_name = city_config.get("name", city_id)
    
    # Imports inside function to ensure paths are loaded and avoid circular dependencies
    from ingestion.cpcb_fetcher import fetch_city_aqi, mock_city_aqi
    from ingestion.quality_checker import check_quality
    from ingestion.weather_fetcher import fetch_city_weather
    from attribution_agent import run_attribution
    from enforcement_agent import run_enforcement
    from forecast_agent import run_forecast, get_aqi_category
    from advisory_agent import run_advisory
    from incident_agent import run_incident_report

    logger.info("[%s] Beginning background pipeline run...", city_id)
    try:
        # ── Step 1: Fetch AQI data ───────────────────────────────────────────
        records = await fetch_city_aqi(city_config)
        if not records:
            logger.warning("[%s] Live AQI fetch returned empty. Using mock data.", city_id)
            records = mock_city_aqi(city_config)
            
        records = [check_quality(r) for r in records]
        
        # ── Step 2: Fetch weather ────────────────────────────────────────────
        weather = await fetch_city_weather(city_config)
        
        # ── Step 3: Write health status to Redis ─────────────────────────────
        stations_health = {}
        for r in records:
            sid = r.get("station_id", "unknown")
            stations_health[sid] = {
                "quality_score": r.get("quality_score", 1.0),
                "flags":         r.get("quality_flags", []),
                "aqi":           r.get("aqi")
            }
            
        # Determine AQI data source flag
        aqi_source = "live" if any(r.get("data_source") == "live" for r in records) else "mock"

        health_blob = {
            "last_fetch":    datetime.now(timezone.utc).isoformat(),
            "station_count": len(records),
            "stations":      stations_health,
            "data_source":   aqi_source
        }
        await set_json(f"health:{city_id}", health_blob, ttl_seconds=2100) # 35 min TTL
        
        # ── Step 4: Run Ward Analysis Blobs ──────────────────────────────────
        ward_aqi_map = {}
        wards = city_config.get("wards", [])
        
        for w in wards:
            w_lat = w.lat if hasattr(w, "lat") else w["lat"]
            w_lon = w.lon if hasattr(w, "lon") else w["lon"]
            w_id = w.ward_id if hasattr(w, "ward_id") else w["ward_id"]
            w_name = w.name if hasattr(w, "name") else w["name"]
            
            # Find closest monitoring station
            closest_rec = None
            min_dist = float("inf")
            for r in records:
                r_lat = r.get("lat")
                r_lon = r.get("lon")
                if r_lat is not None and r_lon is not None:
                    dist = geodesic((w_lat, w_lon), (r_lat, r_lon)).kilometers
                    if dist < min_dist:
                        min_dist = dist
                        closest_rec = r
                        
            if not closest_rec:
                closest_rec = records[0]
                
            aqi = closest_rec.get("aqi", 100)
            quality_mod = closest_rec.get("confidence_modifier", 1.0)
            ward_aqi_map[w_id] = aqi
            
            # 4b. Run attribution
            wind_direction = weather.get("current", {}).get("wind_direction", 180.0)
            attribution_result = await run_attribution(
                station_lat=w_lat,
                station_lon=w_lon,
                current_aqi=aqi,
                wind_direction=wind_direction,
                quality_modifier=quality_mod,
                city_config=city_config
            )
            
            # 4c. Run enforcement
            enforcement_result = run_enforcement(attribution_result, aqi, city_config)
            
            # 4d. Run forecast
            forecast_result = await run_forecast(
                current_aqi=aqi,
                current_conditions=weather.get("current", {}),
                weather_forecast_72h=weather.get("forecast_72h", []),
                city_config=city_config
            )
            
            # 4e. Run advisory (uses 3rd forecast item [index 2] category → 6h forecast)
            fc_6h_category = forecast_result["forecast"][2]["category"]
            advisory_result = await run_advisory(
                ward=w.model_dump() if hasattr(w, "model_dump") else w,
                current_aqi=aqi,
                aqi_category=fc_6h_category,
                forecast_peak_24h=forecast_result["peak_24h"],
                city_config=city_config,
                redis_client=redis_client
            )
            
            # 4f. Run incident commander report
            incident_result = None
            if get_aqi_category(aqi) in ["Very Poor", "Severe"]:
                incident_result = await run_incident_report(
                    ward=w.model_dump() if hasattr(w, "model_dump") else w,
                    current_aqi=aqi,
                    attribution_output=attribution_result,
                    enforcement_output=enforcement_result,
                    forecast_output=forecast_result,
                    city_config=city_config,
                    redis_client=redis_client
                )
                
            # Write full Ward analysis package to Redis
            ward_blob = {
                "aqi":             aqi,
                "category":        get_aqi_category(aqi),
                "attribution":     attribution_result,
                "enforcement":     enforcement_result,
                "forecast":        forecast_result,
                "advisory":        advisory_result,
                "incident_report": incident_result,
                "updated_at":      datetime.now(timezone.utc).isoformat(),
                "data_source":     closest_rec.get("data_source", "mock")
            }
            await set_json(f"ward:{city_id}:{w_id}", ward_blob, ttl_seconds=2100) # 35 min TTL
            
        # ── Step 5: Write city summary to Redis ─────────────────────────────
        summary_wards = []
        for w in wards:
            w_lat = w.lat if hasattr(w, "lat") else w["lat"]
            w_lon = w.lon if hasattr(w, "lon") else w["lon"]
            w_id = w.ward_id if hasattr(w, "ward_id") else w["ward_id"]
            w_name = w.name if hasattr(w, "name") else w["name"]
            ward_aqi = ward_aqi_map.get(w_id, 100)
            
            summary_wards.append({
                "ward_id":  w_id,
                "name":     w_name,
                "aqi":      ward_aqi,
                "category": get_aqi_category(ward_aqi),
                "lat":      w_lat,
                "lon":      w_lon
            })
            
        summary_blob = {
            "city_id":    city_id,
            "name":       city_name,
            "wards":      summary_wards,
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "data_source": aqi_source
        }
        await set_json(f"summary:{city_id}", summary_blob, ttl_seconds=2100) # 35 min TTL
        
        logger.info("[%s] Pipeline run completed successfully.", city_id)
        
    except Exception as exc:
        logger.error("[%s] Error running background intelligence pipeline: %s", city_id, exc, exc_info=True)


# ---------------------------------------------------------------------------
# Background trigger task
# ---------------------------------------------------------------------------

async def _scheduler_tick() -> None:
    """Executes the ingestion + agent pipeline for all cities concurrently."""
    logger.info("=== Starting scheduled pipeline run for all cities ===")
    from city_loader import load_all_cities
    cities = load_all_cities()
    
    if not cities:
        logger.warning("No city configurations loaded. Skipping pipeline run.")
        return
        
    # Run pipelines sequentially to prevent CPCB API rate-limiting/timeouts
    city_configs_dict = {cid: (cfg.model_dump() if hasattr(cfg, "model_dump") else cfg) for cid, cfg in cities.items()}
    for cfg in city_configs_dict.values():
        await run_city_pipeline(cfg)
        await asyncio.sleep(2.0)
    logger.info("=== Scheduled pipeline run completed ===")


# ---------------------------------------------------------------------------
# Lifecycle helpers (called from main.py lifespan)
# ---------------------------------------------------------------------------

async def start_scheduler() -> None:
    """Register jobs and start the scheduler."""
    _scheduler.add_job(
        _scheduler_tick,
        trigger="interval",
        minutes=settings.scheduler_interval_minutes,
        id="city_jobs_tick",
        replace_existing=True,
    )
    _scheduler.start()
    logger.info(
        "Scheduler started — pipeline ticks scheduled every %d minutes.",
        settings.scheduler_interval_minutes,
    )


async def stop_scheduler() -> None:
    """Gracefully shut down the scheduler."""
    if _scheduler.running:
        _scheduler.shutdown(wait=False)
        logger.info("Scheduler stopped.")


def scheduler_status() -> str:
    """Return 'running' or 'stopped'."""
    return "running" if _scheduler.running else "stopped"
