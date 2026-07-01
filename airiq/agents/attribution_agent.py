"""
agents/attribution_agent.py
-----------------------------
Agent 1 — Source Attribution

Given an AQI reading at a station and the current wind direction, scores
every emission source in the city to determine which is most likely
responsible for the elevated pollution.

Algorithm (deterministic, no ML needed):
  For each emission source:
    1. Compute distance_km  (geopy geodesic)
    2. Compute bearing from source → station
    3. wind_alignment = cos(wind_dir − bearing)  clamped to [0, 1]
    4. raw_score = (1 / (1 + distance_km)) × wind_alignment × source.intensity
    5. final_score = raw_score × quality_modifier
  Normalise so top source = 100 %, others proportional.

Public API:
  run_attribution(station_lat, station_lon, current_aqi, wind_direction,
                  quality_modifier, city_config) → dict
"""

import logging
import math
from typing import Any

import httpx
from geopy.distance import geodesic

from config import settings

logger = logging.getLogger("airiq.attribution_agent")

# ---------------------------------------------------------------------------
# Geometry helpers
# ---------------------------------------------------------------------------

def _bearing_deg(src_lat: float, src_lon: float,
                 dst_lat: float, dst_lon: float) -> float:
    """
    Compute the initial compass bearing (degrees, 0 = North) from
    (src_lat, src_lon) → (dst_lat, dst_lon).
    """
    lat1 = math.radians(src_lat)
    lat2 = math.radians(dst_lat)
    dlon = math.radians(dst_lon - src_lon)

    x = math.sin(dlon) * math.cos(lat2)
    y = (math.cos(lat1) * math.sin(lat2)
         - math.sin(lat1) * math.cos(lat2) * math.cos(dlon))

    bearing = math.degrees(math.atan2(x, y))
    return (bearing + 360) % 360


def _wind_alignment(wind_direction: float, bearing_source_to_station: float) -> float:
    """
    Return how directly upwind the source is relative to the station.
    1.0 = source is directly upwind (wind blows straight from source to station)
    0.0 = crosswind or downwind
    """
    # Wind direction: the direction wind is blowing *from* (meteorological)
    # bearing_source_to_station: direction from source to station
    # Alignment is highest when wind_direction ≈ bearing_source_to_station
    angle_diff = wind_direction - bearing_source_to_station
    alignment = math.cos(math.radians(angle_diff))
    return max(0.0, alignment)   # clamp negatives to 0


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------

def _score_source(
    source: Any,                   # EmissionSource model instance or dict
    station_lat: float,
    station_lon: float,
    wind_direction: float,
    quality_modifier: float,
) -> dict:
    """Compute attribution metrics for a single emission source."""
    # Support both Pydantic model and plain dict
    if hasattr(source, "lat"):
        src_lat, src_lon = source.lat, source.lon
        name       = source.name
        src_type   = source.type
        intensity  = source.intensity
    else:
        src_lat, src_lon = source["lat"], source["lon"]
        name       = source["name"]
        src_type   = source["type"]
        intensity  = source["intensity"]

    # 1. Distance
    distance_km = geodesic(
        (src_lat, src_lon), (station_lat, station_lon)
    ).kilometers

    # 2. Bearing from source → station
    bearing = _bearing_deg(src_lat, src_lon, station_lat, station_lon)

    # 3. Wind alignment
    alignment = _wind_alignment(wind_direction, bearing)

    # 4. Raw score
    raw_score = (1.0 / (1.0 + distance_km)) * alignment * intensity

    # 5. Apply data-quality modifier
    final_score = raw_score * quality_modifier

    return {
        "source_name":    name,
        "source_type":    src_type,
        "raw_score":      round(raw_score, 6),
        "final_score":    round(final_score, 6),
        "distance_km":    round(distance_km, 2),
        "wind_alignment": round(alignment, 4),
        "bearing":        round(bearing, 1),
        "intensity":      intensity,
        # confidence_pct filled in after normalisation
        "confidence_pct": 0.0,
        "is_primary":     False,
    }


def _normalise_scores(scored: list[dict]) -> list[dict]:
    """Scale confidence_pct so the top source = 100 %."""
    if not scored:
        return scored
    max_score = max(s["final_score"] for s in scored)
    if max_score == 0:
        return scored
    for s in scored:
        s["confidence_pct"] = round((s["final_score"] / max_score) * 100, 1)
    scored[0]["is_primary"] = True   # list is sorted desc before this call
    return scored


# ---------------------------------------------------------------------------
# Claude explanation (optional — gracefully skipped if no API key)
# ---------------------------------------------------------------------------

async def _claude_explanation(
    top_source: dict,
    current_aqi: int,
    wind_direction: float,
    city_name: str,
) -> str:
    """Ask Claude for a single plain-English sentence explaining the attribution."""
    api_key = settings.anthropic_api_key.strip()
    if not api_key:
        return (
            f"AQI of {current_aqi} is likely driven by {top_source['source_name']} "
            f"({top_source['distance_km']} km away, wind from {wind_direction:.0f}°)."
        )

    prompt = (
        f"In one plain English sentence (max 25 words), explain why an AQI of {current_aqi} "
        f"in {city_name} is primarily attributed to {top_source['source_name']} "
        f"({top_source['source_type']}, {top_source['distance_km']} km upwind, "
        f"wind direction {wind_direction:.0f}°, confidence {top_source['confidence_pct']:.0f}%). "
        "Be direct and specific."
    )

    try:
        import anthropic
        client = anthropic.AsyncAnthropic(api_key=api_key)
        msg = await client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=80,
            messages=[{"role": "user", "content": prompt}],
        )
        return msg.content[0].text.strip()
    except Exception as exc:
        logger.warning("Claude explanation failed: %s — using fallback.", exc)
        return (
            f"AQI of {current_aqi} is likely driven by {top_source['source_name']} "
            f"({top_source['distance_km']} km upwind, {top_source['confidence_pct']:.0f}% confidence)."
        )


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

async def run_attribution(
    station_lat: float,
    station_lon: float,
    current_aqi: int,
    wind_direction: float,        # degrees, 0=N, 90=E, 180=S, 270=W
    quality_modifier: float,      # from quality_checker (0.5–1.0)
    city_config: Any,             # CityConfig model or dict
) -> dict:
    """
    Run source attribution for a single station reading.

    Parameters
    ----------
    station_lat / station_lon : station coordinates
    current_aqi               : AQI value at this station
    wind_direction            : meteorological wind direction (degrees)
    quality_modifier          : data quality weight (from quality_checker)
    city_config               : CityConfig model or plain dict from city JSON

    Returns
    -------
    {
      "sources"     : [...all sources sorted by confidence desc...],
      "top_source"  : {...},
      "explanation" : "Claude-generated 1-sentence explanation",
      "aqi"         : int,
      "wind_direction": float,
    }
    """
    # Resolve emission_sources from model or dict
    if hasattr(city_config, "emission_sources"):
        sources = city_config.emission_sources
        city_name = getattr(city_config, "display_name", str(city_config))
    else:
        sources = city_config.get("emission_sources", [])
        city_name = city_config.get("display_name", city_config.get("city_id", "city"))

    if not sources:
        logger.warning("No emission sources configured for %s", city_name)
        return {
            "sources":      [],
            "top_source":   None,
            "explanation":  "No emission sources configured for this city.",
            "aqi":          current_aqi,
            "wind_direction": wind_direction,
        }

    # Score every source
    scored = [
        _score_source(src, station_lat, station_lon, wind_direction, quality_modifier)
        for src in sources
    ]

    # Sort descending by final_score
    scored.sort(key=lambda s: s["final_score"], reverse=True)

    # Normalise to confidence %
    scored = _normalise_scores(scored)

    top = scored[0]

    # Generate explanation
    explanation = await _claude_explanation(top, current_aqi, wind_direction, city_name)

    logger.info(
        "[%s] attribution — AQI %d | top=%s (%.0f%%) | wind=%.0f°",
        city_name, current_aqi, top["source_name"], top["confidence_pct"], wind_direction,
    )

    return {
        "sources":        scored,
        "top_source":     top,
        "explanation":    explanation,
        "aqi":            current_aqi,
        "wind_direction": wind_direction,
    }
