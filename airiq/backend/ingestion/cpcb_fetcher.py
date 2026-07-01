"""
backend/ingestion/cpcb_fetcher.py
-----------------------------------
Fetches real-time AQI data from the CPCB / data.gov.in API.

Matches real station names, groups/pivots pollutant parameter rows,
calculates dynamic AQI, and tags data source as "live" or "mock".
"""

import logging
import random
import asyncio
from datetime import datetime, timezone

import httpx

from config import settings

logger = logging.getLogger("airiq.cpcb_fetcher")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
CPCB_BASE_URL = "https://api.data.gov.in/resource"
REQUEST_TIMEOUT = 20          # seconds
MAX_RECORDS_PER_STATION = 10  # latest readings


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _safe_float(val) -> float | None:
    try:
        return float(val)
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# Indian AQI Breakpoints Calculator (PM2.5 based)
# ---------------------------------------------------------------------------
def calculate_aqi_from_pm25(pm25: float) -> int:
    """
    Computes Indian National AQI from PM2.5 concentrations (24h avg, ug/m3)
    using the official CPCB breakpoints:
      0-30   -> 0-50
      31-60  -> 51-100
      61-90  -> 101-200
      91-120 -> 201-300
      121-250-> 301-400
      251+   -> 401-500
    Linear interpolation within each band.
    """
    val = pm25
    if val <= 0:
        return 0
    elif val <= 30:
        aqi = 0 + (val - 0) * (50 - 0) / (30 - 0)
    elif val <= 60:
        aqi = 51 + (val - 30) * (100 - 51) / (60 - 30)
    elif val <= 90:
        aqi = 101 + (val - 60) * (200 - 101) / (90 - 60)
    elif val <= 120:
        aqi = 201 + (val - 90) * (300 - 201) / (120 - 90)
    elif val <= 250:
        aqi = 301 + (val - 120) * (400 - 301) / (250 - 120)
    else:
        # CPCB PM2.5 severe band goes up to 380 ug/m3 for 500 AQI
        if val <= 380:
            aqi = 401 + (val - 250) * (500 - 401) / (380 - 250)
        else:
            aqi = 500
    return int(round(aqi))


# ---------------------------------------------------------------------------
# Real fetcher (Sequential, Station-Filtered, Grouped/Pivoted)
# ---------------------------------------------------------------------------
async def fetch_city_aqi(city_config: dict) -> list[dict]:
    """
    Fetch live AQI sequentially for all stations in city_config["station_ids"]
    by querying the CPCB API with filters[station]=<station_name>.
    Pivots pollutant parameter rows and calculates AQI.
    Falls back to mock data per station if offline.
    """
    api_key = settings.cpcb_api_key.strip()
    resource_id = city_config.get("cpcb_resource_id", "3b01bcb8-0b14-4abf-b6f2-c1bfd384ba69")
    url = f"{CPCB_BASE_URL}/{resource_id}"
    city_id = city_config["city_id"]

    if not api_key:
        logger.warning("[%s] CPCB_API_KEY not set — returning mock data.", city_id)
        return mock_city_aqi(city_config)

    results: list[dict] = []
    stations_cfg = city_config.get("stations", {})
    now_iso = datetime.now(timezone.utc).isoformat()

    async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
        for idx, station_name in enumerate(city_config.get("station_ids", [])):
            meta = stations_cfg.get(station_name, {})
            
            # Rate-limiting spacer (1s) between station queries
            if idx > 0:
                await asyncio.sleep(1.0)
            
            params = {
                "api-key":         api_key,
                "format":          "json",
                "limit":           50,
                "filters[station]": station_name,
            }

            pivoted = {
                "pm2.5": None, "pm10": None, "no2": None, "so2": None,
                "timestamp": None
            }
            success = False

            try:
                logger.info("[%s] Fetching CPCB data for station: \"%s\"", city_id, station_name)
                resp = await client.get(url, params=params)
                resp.raise_for_status()
                payload = resp.json()
                records = payload.get("records", [])

                if records:
                    success = True
                    for r in records:
                        param = (r.get("parameter") or "").lower().strip()
                        val = _safe_float(r.get("value"))
                        pivoted["timestamp"] = r.get("last_update") or r.get("from_date")
                        
                        if val is not None:
                            if param in ["pm2.5", "pm25"]:
                                pivoted["pm2.5"] = val
                            elif param == "pm10":
                                pivoted["pm10"] = val
                            elif param == "no2":
                                pivoted["no2"] = val
                            elif param == "so2":
                                pivoted["so2"] = val

            except Exception as exc:
                logger.warning("[%s] Error fetching CPCB for station \"%s\": %s", city_id, station_name, exc)

            # Check if we got valid pollutants
            if success and (pivoted["pm2.5"] is not None or pivoted["pm10"] is not None):
                pm25 = pivoted["pm2.5"]
                
                # Compute AQI from PM2.5
                if pm25 is not None:
                    aqi = calculate_aqi_from_pm25(pm25)
                else:
                    # Fallback to PM10 estimate if PM2.5 missing
                    aqi = int((pivoted["pm10"] or 100.0) / 0.90)
                
                results.append({
                    "station_id":  station_name,
                    "aqi":         aqi,
                    "pm25":        pm25 or (aqi * 0.55),
                    "pm10":        pivoted["pm10"] or (aqi * 0.90),
                    "no2":         pivoted["no2"] or 20.0,
                    "timestamp":   pivoted["timestamp"] or now_iso,
                    "lat":         meta.get("lat", city_config.get("lat")),
                    "lon":         meta.get("lon", city_config.get("lon")),
                    "city_id":     city_id,
                    "data_source": "live"
                })
                logger.info("[%s] Real CPCB fetch successful for \"%s\" → AQI: %d (PM2.5=%s)", 
                            city_id, station_name, aqi, pm25)
            else:
                logger.warning("[%s] No live pollutant rows for \"%s\". Falling back to mock.", city_id, station_name)
                results.append(mock_single_station(city_id, station_name, meta, now_iso))

    logger.info("[%s] fetch_city_aqi complete — %d/%d stations successfully compiled.", 
                city_id, sum(1 for r in results if r["data_source"] == "live"), len(city_config.get("station_ids", [])))
    return results


# ---------------------------------------------------------------------------
# Mock fallbacks — robust demo-day fallback
# ---------------------------------------------------------------------------
def mock_single_station(city_id: str, station_name: str, meta: dict, timestamp: str) -> dict:
    """Helper to mock a single station's data tagged as mock."""
    baselines = {"delhi": 115, "mumbai": 58, "bengaluru": 52, "kolkata": 58}
    base_aqi = baselines.get(city_id, 120)
    
    # Deterministic seed based on station name string
    seed_val = sum(ord(c) for c in station_name)
    random.seed(seed_val)
    
    aqi = max(10, base_aqi + random.randint(-30, 45))
    pm25 = round(aqi * 0.55 + random.uniform(-4, 4), 1)
    pm10 = round(aqi * 0.90 + random.uniform(-8, 8), 1)
    no2 = round(random.uniform(15, 65), 1)
    
    # reset random seed
    random.seed()
    
    return {
        "station_id": station_name,
        "aqi":        aqi,
        "pm25":       pm25,
        "pm10":       pm10,
        "no2":        no2,
        "timestamp":  timestamp,
        "lat":        meta.get("lat", 12.9),
        "lon":        meta.get("lon", 77.5),
        "city_id":    city_id,
        "data_source": "mock"
    }


def mock_city_aqi(city_config: dict) -> list[dict]:
    """Generates mock fallback list for all stations in city_config."""
    city_id = city_config["city_id"]
    stations_cfg = city_config.get("stations", {})
    now_iso = datetime.now(timezone.utc).isoformat()
    
    results = []
    for station_name in city_config.get("station_ids", []):
        meta = stations_cfg.get(station_name, {})
        results.append(mock_single_station(city_id, station_name, meta, now_iso))
        
    logger.info("[%s] mock_city_aqi — generated %d fallback records.", city_id, len(results))
    return results
