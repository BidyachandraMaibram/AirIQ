"""
agents/advisory_agent.py
-------------------------
Agent 4 — Citizen Advisory Agent

Generates localized, multi-lingual public health advisories for wards.
Caches generated advisories to minimize Anthropic LLM API call costs,
only regenerating when the AQI category for the ward changes.
"""

import logging
import datetime
from typing import Any
import anthropic
from geopy.distance import geodesic

from config import settings
from cache import get_json, set_json

logger = logging.getLogger("airiq.advisory_agent")

# ── Language Code to Name map ───────────────────────────────────────────────
LANGUAGES = {
    "kn": "Kannada",
    "mr": "Marathi",
    "hi": "Hindi",
    "bn": "Bengali",
    "en": "English"
}

# ── Localised action advisory fallbacks ──────────────────────────────────────
FALLBACK_ADVICE = {
    "Good": "Air quality is safe.",
    "Satisfactory": "Air quality is acceptable.",
    "Moderate": "Sensitive groups should limit outdoor activity.",
    "Poor": "Avoid outdoor exercise. Wear N95 mask.",
    "Very Poor": "Stay indoors. Keep windows closed.",
    "Severe": "Do not go outdoors. Seek medical help if breathing difficulty."
}


# ---------------------------------------------------------------------------
# Close sites filtering
# ---------------------------------------------------------------------------
def _get_nearby_vulnerable_sites(ward: dict, city_config: dict, max_km: float = 3.0) -> list[str]:
    """Find vulnerable sites in the city within max_km distance of the ward centroid."""
    ward_coords = (ward.get("lat", 0.0), ward.get("lon", 0.0))
    nearby = []
    
    # Support both model object and raw dict
    if hasattr(city_config, "vulnerable_sites"):
        sites = city_config.vulnerable_sites
    else:
        sites = city_config.get("vulnerable_sites", [])
        
    for s in sites:
        if hasattr(s, "lat"):
            site_coords = (s.lat, s.lon)
            name, site_type = s.name, s.type
        else:
            site_coords = (s["lat"], s["lon"])
            name, site_type = s["name"], s["type"]
            
        dist = geodesic(ward_coords, site_coords).kilometers
        if dist <= max_km:
            nearby.append(f"{name} ({site_type})")
            
    return nearby


# ---------------------------------------------------------------------------
# Public async advisor
# ---------------------------------------------------------------------------
async def run_advisory(
    ward: dict,
    current_aqi: int,
    aqi_category: str,
    forecast_peak_24h: dict,    # {aqi, hour, category}
    city_config: dict,
    redis_client: Any = None     # placeholder argument for pipeline
) -> dict:
    """
    Produces a 2-sentence public health advisory for a specific ward.
    
    Uses Redis caching to avoid calling Claude if the AQI category
    for this ward hasn't changed.
    """
    city_id = city_config.get("city_id", "unknown")
    ward_id = ward.get("ward_id", "unknown")
    ward_name = ward.get("name", "Unknown Ward")
    lang_code = city_config.get("language", "en")
    lang_name = LANGUAGES.get(lang_code, "English")
    
    cache_key = f"advisory:{city_id}:{ward_id}"
    
    # ── 1. Cache Check ───────────────────────────────────────────────────────
    try:
        cached = await get_json(cache_key)
        if cached and cached.get("aqi_category") == aqi_category:
            logger.info("[%s:%s] Advisory hit in cache.", city_id, ward_id)
            cached["from_cache"] = True
            return cached
    except Exception as exc:
        logger.warning("Cache fetch failed for advisory key: %s", exc)

    # ── 2. Render Prompt Context ─────────────────────────────────────────────
    nearby_sites = _get_nearby_vulnerable_sites(ward, city_config, max_km=3.0)
    sites_str = ", ".join(nearby_sites) if nearby_sites else "None nearby"
    
    peak_aqi = forecast_peak_24h.get("aqi", current_aqi)
    peak_cat = forecast_peak_24h.get("category", aqi_category)
    peak_hour = forecast_peak_24h.get("hour", 0)
    
    system_prompt = f"You are a public health communicator for Indian cities. Write only in {lang_name}. Be concise and calm."
    user_prompt = (
        f"Ward: {ward_name}. Current AQI: {current_aqi} ({aqi_category}). "
        f"Forecast peak in 24 hours: {peak_aqi} ({peak_cat}) around {peak_hour}:00. "
        f"Vulnerable sites nearby: {sites_str}. "
        f"Write a 2-sentence public health advisory suitable for IVR voice call and mobile notification. "
        f"Include one specific protective action."
    )

    advisory_text = ""
    api_key = settings.anthropic_api_key.strip()
    
    # ── 3. Call Claude / Fallback ────────────────────────────────────────────
    if api_key:
        try:
            client = anthropic.AsyncAnthropic(api_key=api_key)
            response = await client.messages.create(
                model="claude-sonnet-4-6",
                max_tokens=150,
                messages=[{"role": "user", "content": user_prompt}],
                system=system_prompt
            )
            advisory_text = response.content[0].text.strip()
        except Exception as exc:
            logger.error("Claude call failed for advisory: %s. Using fallback.", exc)
            
    if not advisory_text:
        # Fallback template
        advice = FALLBACK_ADVICE.get(aqi_category, FALLBACK_ADVICE["Moderate"])
        advisory_text = f"AQI in {ward_name} is {current_aqi} ({aqi_category}). {advice}"
        
    # ── 4. Save and Return ───────────────────────────────────────────────────
    result = {
        "ward_id": ward_id,
        "advisory_text": advisory_text,
        "language": lang_name,
        "aqi_category": aqi_category,
        "from_cache": False,
        "generated_at": datetime.datetime.now(datetime.timezone.utc).isoformat()
    }
    
    try:
        # Cache for 1 hour
        await set_json(cache_key, result, ttl_seconds=3600)
    except Exception as exc:
        logger.warning("Cache write failed for advisory key: %s", exc)
        
    return result
