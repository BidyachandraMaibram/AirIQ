"""
agents/incident_agent.py
------------------------
Agent 5 — Incident Commander Agent

Generates structured, government-ready incident reports when AQI spikes
to "Very Poor" or "Severe" levels.

Caches report output for 60 minutes to manage API costs.
"""

import logging
import datetime
import re
from typing import Any
import anthropic

from config import settings
from cache import get_json, set_json

logger = logging.getLogger("airiq.incident_agent")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _get_aqi_category(aqi: int) -> str:
    if aqi <= 50:
        return "Good"
    elif aqi <= 100:
        return "Satisfactory"
    elif aqi <= 200:
        return "Moderate"
    elif aqi <= 300:
        return "Poor"
    elif aqi <= 400:
        return "Very Poor"
    else:
        return "Severe"


def _parse_alert_level(text: str, default: str) -> str:
    """Extract alert level color from report markdown."""
    match = re.search(r"Alert\s+Level:\s*\[?(RED|ORANGE|YELLOW|GREEN)\]?", text, re.IGNORECASE)
    if match:
        return match.group(1).upper()
    return default


# ---------------------------------------------------------------------------
# Main incident agent runner
# ---------------------------------------------------------------------------
async def run_incident_report(
    ward: dict,
    current_aqi: int,
    attribution_output: dict,
    enforcement_output: dict,
    forecast_output: dict,
    city_config: dict,
    redis_client: Any = None
) -> dict | None:
    """
    Generate an incident report if the current AQI category is Very Poor or Severe.
    Returns None otherwise.
    
    Caches the incident report for 60 minutes.
    """
    category = _get_aqi_category(current_aqi)
    
    # ── 1. Trigger Check ─────────────────────────────────────────────────────
    if category not in ["Very Poor", "Severe"]:
        logger.debug("AQI category '%s' is below incident threshold. Skipping report.", category)
        return None
        
    city_id = city_config.get("city_id", "unknown")
    ward_id = ward.get("ward_id", "unknown")
    ward_name = ward.get("name", "Unknown Ward")
    city_name = city_config.get("display_name", "Unknown City")
    
    cache_key = f"incident:{city_id}:{ward_id}"
    
    # ── 2. Cache Check ───────────────────────────────────────────────────────
    try:
        cached = await get_json(cache_key)
        if cached:
            logger.info("[%s:%s] Incident report hit in cache.", city_id, ward_id)
            cached["from_cache"] = True
            return cached
    except Exception as exc:
        logger.warning("Cache fetch failed for incident key: %s", exc)

    # ── 3. Variables preparation ─────────────────────────────────────────────
    timestamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    
    top_source = attribution_output.get("top_source") or {}
    top_source_name = top_source.get("source_name", "Unknown")
    top_source_type = top_source.get("source_type", "other")
    confidence = top_source.get("confidence_pct", 0.0)
    distance = top_source.get("distance_km", 0.0)
    
    actions = enforcement_output.get("actions", [])
    evidence_string = actions[0].get("evidence", "No evidence summary available.") if actions else "No evidence summary available."
    
    peak_aqi = forecast_output.get("peak_24h", {}).get("aqi", current_aqi)
    peak_category = forecast_output.get("peak_24h", {}).get("category", category)
    
    # Calculate hours to peak or display peak target
    hours_to_peak = 12  # default fallback
    for f_item in forecast_output.get("forecast", []):
        if f_item.get("predicted_aqi") == peak_aqi:
            hours_to_peak = f_item.get("hours_ahead", 12)
            break

    action_count = enforcement_output.get("total_actions", 0)
    critical_count = enforcement_output.get("critical_count", 0)
    
    default_alert = "RED" if category == "Severe" else "ORANGE"
    
    # ── 4. Prompt construction ───────────────────────────────────────────────
    system_prompt = "You are an AI Incident Commander for urban pollution control. Write structured government-ready reports in formal English."
    user_prompt = f"""
Generate a pollution incident report for municipal authorities.

Ward: {ward_name}, {city_name}
Current AQI: {current_aqi} ({category})
Time: {timestamp}

TOP ATTRIBUTION SOURCE: {top_source_name} ({top_source_type}) — {confidence}% confidence, {distance}km away
SUPPORTING EVIDENCE: {evidence_string}

FORECAST: AQI expected to peak at {peak_aqi} ({peak_category}) in {hours_to_peak} hours

ENFORCEMENT QUEUE: {action_count} actions recommended, {critical_count} critical

Write the report with exactly these sections:
## Incident Summary (2 sentences)
## Probable Cause (2–3 sentences with evidence)
## Immediate Recommended Actions (3 bullet points, specific and actionable)
## Estimated AQI Improvement (1 sentence with rough figure)
## Alert Level: [{default_alert}]

Keep total length under 300 words.
"""

    report_markdown = ""
    api_key = settings.anthropic_api_key.strip()
    
    # ── 5. LLM Call / Fallback ───────────────────────────────────────────────
    if api_key:
        try:
            client = anthropic.AsyncAnthropic(api_key=api_key)
            response = await client.messages.create(
                model="claude-sonnet-4-6",
                max_tokens=500,
                messages=[{"role": "user", "content": user_prompt}],
                system=system_prompt
            )
            report_markdown = response.content[0].text.strip()
        except Exception as exc:
            logger.error("Claude call failed for incident report: %s. Using fallback.", exc)
            
    if not report_markdown:
        # Fallback structured report markdown
        report_markdown = f"""## Incident Summary
The ward of {ward_name} is experiencing an emergency air quality crisis with a current AQI of {current_aqi} ({category}). Municipal commanders have initiated action response protocols to prevent acute public exposure.

## Probable Cause
Elevated pollution is primarily linked to emissions from {top_source_name} ({top_source_type}) located {distance:.1f}km away with {confidence:.0f}% confidence. Current wind patterns support rapid transport of particulate plumes into local residential zones.

## Immediate Recommended Actions
* Suspend all high-dust construction and open combustion in the {ward_name} ward.
* Mobilize SWM teams to execute immediate dust suppression measures along major corridors.
* Dispatch emission inspection teams to stack sites at {top_source_name}.

## Estimated AQI Improvement
Strict enforcement of recommended measures is projected to bring a 10-15% reduction in local PM levels within the next 8 hours.

## Alert Level: {default_alert}"""

    alert_level = _parse_alert_level(report_markdown, default_alert)
    
    # ── 6. Save and Return ───────────────────────────────────────────────────
    result = {
        "ward_id": ward_id,
        "report_markdown": report_markdown,
        "alert_level": alert_level,
        "generated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "from_cache": False
    }
    
    try:
        # Cache for 60 minutes
        await set_json(cache_key, result, ttl_seconds=3600)
    except Exception as exc:
        logger.warning("Cache write failed for incident key: %s", exc)
        
    return result
