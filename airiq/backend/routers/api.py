"""
routers/api.py — AirIQ Primary Query API.

All endpoints here run under a 10ms response target because they perform
ONLY Redis reads (or in-memory dictionary fallbacks). All complex agent
attribution, forecasting, advisory, and incident commander logic is
offloaded to the background scheduler.
"""

import datetime
from fastapi import APIRouter, HTTPException, Request
from cache import get_json, redis_status
from scheduler import scheduler_status

router = APIRouter(prefix="/api", tags=["API"])


# ---------------------------------------------------------------------------
# GET /api/cities
# ---------------------------------------------------------------------------
@router.get("/cities", summary="List all cities from configurations")
async def get_cities(request: Request):
    """
    Returns the list of all configured cities loaded in app memory at startup.
    """
    cities_map = getattr(request.app.state, "cities", {})
    return [
        {
            "city_id":      cfg.city_id,
            "display_name": cfg.display_name,
            "language":     cfg.language,
            "lat":          cfg.lat,
            "lon":          cfg.lon,
        }
        for cfg in cities_map.values()
    ]


# ---------------------------------------------------------------------------
# GET /api/city/{city_id}/summary
# ---------------------------------------------------------------------------
@router.get("/city/{city_id}/summary", summary="Get real-time city summary")
async def get_city_summary(city_id: str, request: Request):
    """
    Retrieves the latest cached city summary including ward AQI levels.
    """
    # Verify city exists in config
    cities_map = getattr(request.app.state, "cities", {})
    if city_id not in cities_map:
        raise HTTPException(
            status_code=404,
            detail=f"City '{city_id}' is not monitored. Configured: {list(cities_map.keys())}"
        )

    summary = await get_json(f"summary:{city_id}")
    if not summary:
        raise HTTPException(
            status_code=404,
            detail="Data not yet available, scheduler runs every 30 min"
        )
    return summary


# ---------------------------------------------------------------------------
# GET /api/ward/{city_id}/{ward_id}
# ---------------------------------------------------------------------------
@router.get("/ward/{city_id}/{ward_id}", summary="Get full ward analysis and advisory")
async def get_ward_data(city_id: str, ward_id: str, request: Request):
    """
    Retrieves the full analysis blob for a ward:
    AQI, category, source attribution, enforcement queue, forecast, and advisories.
    """
    # Verify city/ward configs exist
    cities_map = getattr(request.app.state, "cities", {})
    city = cities_map.get(city_id)
    if not city:
        raise HTTPException(status_code=404, detail=f"City '{city_id}' not found.")
        
    ward_exists = any(w.ward_id == ward_id for w in city.wards)
    if not ward_exists:
        raise HTTPException(status_code=404, detail=f"Ward '{ward_id}' not found in city '{city_id}'.")

    ward_data = await get_json(f"ward:{city_id}:{ward_id}")
    if not ward_data:
        raise HTTPException(
            status_code=404,
            detail="Ward data not yet cached. Please wait for the initial ingestion run."
        )
    return ward_data


# ---------------------------------------------------------------------------
# GET /api/incident/{city_id}/{ward_id}
# ---------------------------------------------------------------------------
@router.get("/api/incident/{city_id}/{ward_id}", summary="Get Incident Commander report")
@router.get("/incident/{city_id}/{ward_id}", summary="Get Incident Commander report (clean route)")
async def get_incident_report(city_id: str, ward_id: str, request: Request):
    """
    Retrieves the cached Incident Commander report markdown.
    If the ward is below trigger levels, returns an explanation.
    """
    # Verify city/ward configs exist
    cities_map = getattr(request.app.state, "cities", {})
    city = cities_map.get(city_id)
    if not city:
        raise HTTPException(status_code=404, detail=f"City '{city_id}' not found.")
        
    ward_exists = any(w.ward_id == ward_id for w in city.wards)
    if not ward_exists:
        raise HTTPException(status_code=404, detail=f"Ward '{ward_id}' not found in city '{city_id}'.")

    incident = await get_json(f"incident:{city_id}:{ward_id}")
    if not incident:
        return {
            "available": False,
            "reason": "Only generated for Very Poor or Severe AQI wards"
        }
    return incident


# ---------------------------------------------------------------------------
# GET /api/health
# ---------------------------------------------------------------------------
@router.get("/health", summary="Readiness probe with nested city details")
async def get_health(request: Request):
    """
    Aggregates the real-time cache ingestion status across all configured cities.
    """
    r_status = await redis_status()
    s_status = scheduler_status()

    cities_map = getattr(request.app.state, "cities", {})
    cities_health = {}

    for city_id in cities_map.keys():
        h_data = await get_json(f"health:{city_id}")
        if h_data:
            cities_health[city_id] = h_data
        else:
            cities_health[city_id] = {
                "last_fetch": None,
                "station_count": 0,
                "stations": {},
                "status": "pending"
            }

    overall = "ok" if r_status == "ok" and s_status == "running" else "degraded"

    return {
        "status": overall,
        "redis": r_status,
        "scheduler": s_status,
        "cities": cities_health,
        "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat()
    }
