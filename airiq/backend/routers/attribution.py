"""
routers/attribution.py — Source Attribution API endpoint.

  POST /api/attribution  → run attribution for a given station + AQI + wind
"""

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

router = APIRouter(prefix="/api/attribution", tags=["Attribution"])


# ---------------------------------------------------------------------------
# Request / Response schemas
# ---------------------------------------------------------------------------

class AttributionRequest(BaseModel):
    city_id:          str
    station_lat:      float
    station_lon:      float
    current_aqi:      int   = Field(..., ge=0, le=999)
    wind_direction:   float = Field(..., ge=0, le=360, description="Degrees, 0=N")
    quality_modifier: float = Field(1.0,  ge=0.5, le=1.0)


# ---------------------------------------------------------------------------
# POST /api/attribution
# ---------------------------------------------------------------------------

@router.post("", summary="Run source attribution for a station reading")
async def run_attribution(body: AttributionRequest, request: Request):
    """
    Scores every emission source in the city and returns ranked attribution.

    - Uses geodesic distance + wind alignment + source intensity.
    - Returns a Claude-generated 1-sentence explanation (or a fallback if no API key).
    - No API key required for the scoring algorithm itself.
    """
    cities = getattr(request.app.state, "cities", {})
    city   = cities.get(body.city_id)

    if not city:
        raise HTTPException(
            status_code=404,
            detail=f"City '{body.city_id}' not found. Available: {list(cities.keys())}",
        )

    # Import here to keep startup fast and avoid circular imports
    import sys, os
    agents_dir = os.path.join(os.path.dirname(__file__), "..", "..", "agents")
    if agents_dir not in sys.path:
        sys.path.insert(0, os.path.abspath(agents_dir))

    from attribution_agent import run_attribution as _run

    result = await _run(
        station_lat=body.station_lat,
        station_lon=body.station_lon,
        current_aqi=body.current_aqi,
        wind_direction=body.wind_direction,
        quality_modifier=body.quality_modifier,
        city_config=city,
    )

    return result
