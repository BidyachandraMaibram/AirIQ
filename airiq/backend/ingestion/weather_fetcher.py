"""
backend/ingestion/weather_fetcher.py
--------------------------------------
Fetches weather data from the Open-Meteo free API (no API key required).

Public API:
  fetch_city_weather(city_config)  → dict   (async, never raises)

Return shape:
  {
    "current": {
      "wind_speed":           float  (km/h),
      "wind_direction":       float  (degrees),
      "boundary_layer_height":float  (metres),
      "humidity":             float  (%)
    },
    "forecast_72h": [
      {"time": <iso>, "wind_speed": ..., "wind_direction": ...,
       "boundary_layer_height": ..., "humidity": ...},
      ...  (72 hourly entries)
    ]
  }
"""

import logging
from datetime import datetime, timezone

import httpx

logger = logging.getLogger("airiq.weather_fetcher")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
OPEN_METEO_URL  = "https://api.open-meteo.com/v1/forecast"
FORECAST_HOURS  = 72
REQUEST_TIMEOUT = 15

# Sensible defaults returned when the API is unreachable
_DEFAULTS = {
    "wind_speed":            5.0,
    "wind_direction":        180.0,
    "boundary_layer_height": 800.0,
    "humidity":              60.0,
}


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------
def _build_default_response(city_id: str, reason: str) -> dict:
    """Return a safe default payload and log a warning."""
    logger.warning("[%s] Using weather defaults — %s", city_id, reason)
    return {
        "current":      dict(_DEFAULTS),
        "forecast_72h": [
            {
                "time":                 f"default_hour_{i}",
                **_DEFAULTS,
            }
            for i in range(FORECAST_HOURS)
        ],
        "_fallback": True,
        "data_source": "mock"
    }


def _extract_current(hourly: dict, idx: int) -> dict:
    """Pull the reading at index `idx` from an hourly block."""
    def _get(key: str) -> float | None:
        vals = hourly.get(key, [])
        if idx < len(vals):
            v = vals[idx]
            try:
                return float(v) if v is not None else None
            except (TypeError, ValueError):
                return None
        return None

    return {
        "wind_speed":            _get("wind_speed_10m")         or _DEFAULTS["wind_speed"],
        "wind_direction":        _get("wind_direction_10m")     or _DEFAULTS["wind_direction"],
        "boundary_layer_height": _get("boundary_layer_height")  or _DEFAULTS["boundary_layer_height"],
        "humidity":              _get("relative_humidity_2m")   or _DEFAULTS["humidity"],
    }


def _build_forecast(times: list, hourly: dict) -> list[dict]:
    """Build the 72-hour forecast list from raw API data."""
    forecast = []
    for i, t in enumerate(times[:FORECAST_HOURS]):
        entry = _extract_current(hourly, i)
        entry["time"] = t
        forecast.append(entry)
    return forecast


# ---------------------------------------------------------------------------
# Public async fetcher
# ---------------------------------------------------------------------------
async def fetch_city_weather(city_config: dict) -> dict:
    """
    Fetch 72-hour hourly weather forecast for a city.

    Parameters
    ----------
    city_config : dict
        Must contain "lat", "lon", and "city_id".

    Returns
    -------
    dict with keys "current" and "forecast_72h".
    Falls back to sensible defaults on any error — never raises.
    """
    city_id = city_config.get("city_id", "unknown")
    lat     = city_config.get("lat")
    lon     = city_config.get("lon")

    if lat is None or lon is None:
        return _build_default_response(city_id, "lat/lon missing from city_config")

    params = {
        "latitude":  lat,
        "longitude": lon,
        "hourly":    ",".join([
            "wind_speed_10m",
            "wind_direction_10m",
            "boundary_layer_height",
            "relative_humidity_2m",
        ]),
        "forecast_days": 3,          # 3 days = 72 hours
        "timezone":      "Asia/Kolkata",
        "wind_speed_unit": "kmh",
    }

    try:
        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
            resp = await client.get(OPEN_METEO_URL, params=params)
            resp.raise_for_status()
            data = resp.json()

    except httpx.HTTPStatusError as exc:
        return _build_default_response(
            city_id, f"HTTP {exc.response.status_code}: {exc}"
        )
    except Exception as exc:
        return _build_default_response(city_id, str(exc))

    # ── Parse response ──────────────────────────────────────────────────────
    try:
        hourly = data.get("hourly", {})
        times  = hourly.get("time", [])

        if not times:
            return _build_default_response(city_id, "API returned empty hourly data")

        # Find the index closest to the current hour for "current" snapshot
        now_str  = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:00")
        try:
            current_idx = times.index(now_str)
        except ValueError:
            current_idx = 0   # fallback to first entry

        current_weather = _extract_current(hourly, current_idx)
        forecast_72h    = _build_forecast(times, hourly)

        logger.info(
            "[LIVE WEATHER] %s fetched successfully — wind %.1f km/h @ %d°, BLH %.0f m, RH %.0f%% (data_source=live)",
            city_id,
            current_weather["wind_speed"],
            current_weather["wind_direction"],
            current_weather["boundary_layer_height"],
            current_weather["humidity"],
        )

        return {
            "current":      current_weather,
            "forecast_72h": forecast_72h,
            "data_source":  "live"
        }

    except Exception as exc:
        return _build_default_response(city_id, f"Parse error: {exc}")
