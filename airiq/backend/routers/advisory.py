"""
routers/advisory.py — Citizen Advisory API endpoint.

  GET /api/advisory/{city_id}/{ward_id}  → Get advisory for a specific ward
"""

from fastapi import APIRouter, HTTPException, Request
from cache import get_json

router = APIRouter(prefix="/api/advisory", tags=["Advisory"])


# ---------------------------------------------------------------------------
# GET /api/advisory/{city_id}/{ward_id}
# ---------------------------------------------------------------------------

@router.get("/{city_id}/{ward_id}", summary="Get public health advisory for a ward")
async def get_ward_advisory(city_id: str, ward_id: str, request: Request):
    """
    Returns a public health advisory tailored to the target ward.
    
    - Finds the closest AQI monitoring station to the ward centroid.
    - Runs the forecast agent to determine peak 24h category.
    - Invokes advisory agent to retrieve/generate multilingual advisory text.
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

    # Get cached weather conditions for forecast parameters
    weather = await get_json(f"weather:{city_id}")
    current_conditions = weather.get("current", {}) if weather else {}
    forecast_72h = weather.get("forecast_72h", []) if weather else []

    # Run forecast to get 24h peak characteristics
    from forecast_agent import run_forecast, get_aqi_category
    forecast_result = await run_forecast(
        current_aqi=current_aqi,
        current_conditions=current_conditions,
        weather_forecast_72h=forecast_72h,
        city_config=city.model_dump()
    )

    # Execute advisory generation/caching pipeline
    from advisory_agent import run_advisory as run_advisory_agent
    result = await run_advisory_agent(
        ward=ward.model_dump(),
        current_aqi=current_aqi,
        aqi_category=get_aqi_category(current_aqi),
        forecast_peak_24h=forecast_result.get("peak_24h", {}),
        city_config=city.model_dump()
    )

    return result
