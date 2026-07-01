"""
agents/enforcement_agent.py
----------------------------
Agent 3 — Enforcement Agent

Translates source attribution outputs into prioritised, actionable
enforcement recommendations for municipal and state agencies.
"""

import logging
from typing import Any

logger = logging.getLogger("airiq.enforcement_agent")

# ── Action Templates mapping source types to agencies and actions ───────────
ACTION_TEMPLATES = {
    "industrial": {
        "action": "Schedule unannounced stack emission inspection",
        "department": "KSPCB / State PCB"
    },
    "traffic": {
        "action": "Deploy traffic management + diesel vehicle diversion",
        "department": "Traffic Police + RTO"
    },
    "construction": {
        "action": "Issue dust suppression compliance order",
        "department": "BBMP / Municipal Corp"
    },
    "waste_burning": {
        "action": "Dispatch field team, issue burning prohibition notice",
        "department": "SWM Department"
    },
    "other": {
        "action": "Investigate and identify source",
        "department": "Pollution Control Board"
    }
}


def run_enforcement(
    attribution_output: dict,
    current_aqi: int,
    city_config: dict
) -> dict:
    """
    Evaluates attribution results and generates a prioritized queue of
    enforcement actions.

    Steps:
      1. Filter sources where confidence_pct >= 40%
      2. Estimate AQI contribution = confidence_pct / 100 * current_aqi * 0.6
      3. Compute priority score = confidence_pct * estimated_aqi_contribution
      4. Sort and map actions based on templates and source types.
    """
    sources = attribution_output.get("sources", [])
    city_id = city_config.get("city_id", "unknown")
    
    actions = []
    critical_count = 0
    
    for s in sources:
        confidence = s.get("confidence_pct", 0.0)
        
        # 1. Filter confidence >= 40
        if confidence < 40.0:
            continue
            
        name = s.get("source_name", "Unknown Source")
        src_type = s.get("source_type", "other")
        
        # 2. Estimated AQI Contribution
        est_contribution = (confidence / 100.0) * current_aqi * 0.6
        
        # 3. Priority score
        score = confidence * est_contribution
        
        # 4. Map source type to action templates
        template = ACTION_TEMPLATES.get(src_type, ACTION_TEMPLATES["other"])
        
        # 5. Determine priority label
        if score > 5000:
            priority = "CRITICAL"
            critical_count += 1
        elif score > 2000:
            priority = "HIGH"
        elif score > 500:
            priority = "MEDIUM"
        else:
            priority = "LOW"
            
        # Get source location from city_config if missing in attribution
        source_lat = s.get("lat")
        source_lon = s.get("lon")
        if source_lat is None or source_lon is None:
            # Search configured emission sources
            for config_src in city_config.get("emission_sources", []):
                cfg_name = config_src.name if hasattr(config_src, "name") else config_src.get("name")
                if cfg_name == name:
                    source_lat = config_src.lat if hasattr(config_src, "lat") else config_src.get("lat")
                    source_lon = config_src.lon if hasattr(config_src, "lon") else config_src.get("lon")
                    break
            
            # Default to city center coordinates if still not found
            if source_lat is None:
                source_lat = city_config.get("lat", 0.0)
                source_lon = city_config.get("lon", 0.0)
                
        # 6. Generate evidence sentence
        distance = s.get("distance_km", 0.0)
        alignment = s.get("wind_alignment", 0.0)
        evidence = f"{confidence:.0f}% confidence, {distance:.1f}km upwind, strong wind alignment (alignment: {alignment:.2f})"
        
        actions.append({
            "priority": priority,
            "source_name": name,
            "source_type": src_type,
            "recommended_action": template["action"],
            "department": template["department"],
            "confidence_pct": round(confidence, 1),
            "estimated_aqi_contribution": round(est_contribution, 1),
            "source_lat": round(source_lat, 5),
            "source_lon": round(source_lon, 5),
            "evidence": evidence,
            "score": round(score, 1)  # internal for sorting
        })
        
    # Sort actions by priority score descending
    actions.sort(key=lambda a: a["score"], reverse=True)
    
    # Strip the helper score from the output
    for a in actions:
        a.pop("score", None)
        
    logger.info(
        "[%s] enforcement queue generated — %d actions, %d critical.",
        city_id, len(actions), critical_count
    )
    
    return {
        "actions": actions,
        "total_actions": len(actions),
        "critical_count": critical_count
    }
