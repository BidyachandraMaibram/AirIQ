"""
routers/incident.py — Incident Commander API endpoint.

  GET /api/incident/{city_id}/{ward_id}  → Get commander report for severe spikes
"""

from fastapi import APIRouter, HTTPException, Request
from cache import get_json

router = APIRouter(prefix="/api/incident", tags=["Incident"])


# ---------------------------------------------------------------------------
# GET /api/incident/{city_id}/{ward_id}
# ---------------------------------------------------------------------------

@router.get("/{city_id}/{ward_id}", summary="Get Incident Commander report for a ward")
async def get_incident_report(city_id: str, ward_id: str, request: Request):
    """
    Returns an Incident Commander report if AQI values trigger the thresholds
    (requires AQI category to be 'Very Poor' or 'Severe').
    
    - Computes attribution, enforcement, and forecast metrics dynamically.
    - Generates or pulls the cached Incident Commander markdown report.
    """
    cities = getattr(request.app.state, "cities", {})
    city   = cities.get(city_id)

    if not city:
        raise HTTPException(
            status_code=404,
            detail=f"City '{city_id}' not found. Available: {list(cities.keys())}",
        )

    # Find ward config details
    ward = None
    for w in city.wards:
        if w.ward_id == ward_id:
            ward = w
            break
            
    if not ward:
        raise HTTPException(
            status_code=404,
            detail=f"Ward '{ward_id}' not found in city '{city_id}'.",
        )

    # Get latest cached AQI records for the city to find the closest station
    aqi_records = await get_json(f"aqi:{city_id}")
    if not aqi_records:
        raise HTTPException(
            status_code=503,
            detail="No AQI data available. Ingestion pipeline may be pending first run.",
        )

    # Find closest monitoring station to the ward centroid coordinates
    from geopy.distance import geodesic
    ward_coords = (ward.lat, ward.lon)
    closest_rec = None
    min_dist = float("inf")
    
    for rec in aqi_records:
        rec_coords = (rec.get("lat"), rec.get("lon"))
        if rec_coords[0] is not None and rec_coords[1] is not None:
            dist = geodesic(ward_coords, rec_coords).kilometers
            if dist < min_dist:
                min_dist = dist
                closest_rec = rec

    if not closest_rec:
        closest_rec = aqi_records[0]

    current_aqi = closest_rec.get("aqi", 100)

    # Check trigger condition
    from forecast_agent import get_aqi_category
    category = get_aqi_category(current_aqi)
    if category not in ["Very Poor", "Severe"]:
        return {
            "triggered": False,
            "message": (
                f"Incident report only triggered for 'Very Poor' or 'Severe' AQI. "
                f"Current category for ward '{ward.name}' is '{category}' ({current_aqi})."
            ),
            "report_markdown": None,
            "alert_level": "GREEN" if category in ["Good", "Satisfactory"] else "YELLOW"
        }

    # Get cached weather conditions
    weather = await get_json(f"weather:{city_id}")
    current_conditions = weather.get("current", {}) if weather else {}
    forecast_72h = weather.get("forecast_72h", []) if weather else []

    # 1. Run Attribution Agent
    from attribution_agent import run_attribution
    result_attr = await run_attribution(
        station_lat=closest_rec.get("lat"),
        station_lon=closest_rec.get("lon"),
        current_aqi=current_aqi,
        wind_direction=current_conditions.get("wind_direction", 180.0),
        quality_modifier=closest_rec.get("confidence_modifier", 1.0),
        city_config=city.model_dump()
    )

    # 2. Run Enforcement Agent
    from enforcement_agent import run_enforcement
    result_enf = run_enforcement(result_attr, current_aqi, city.model_dump())

    # 3. Run Forecast Agent
    from forecast_agent import run_forecast
    result_fc = await run_forecast(
        current_aqi=current_aqi,
        current_conditions=current_conditions,
        weather_forecast_72h=forecast_72h,
        city_config=city.model_dump()
    )

    # 4. Run Incident Commander Agent
    from incident_agent import run_incident_report as run_incident_agent
    result = await run_incident_agent(
        ward=ward.model_dump(),
        current_aqi=current_aqi,
        attribution_output=result_attr,
        enforcement_output=result_enf,
        forecast_output=result_fc,
        city_config=city.model_dump()
    )

    return {
        "triggered": True,
        "message": "Incident report generated successfully.",
        **result
    }
