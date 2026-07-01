"""
routers/forecast.py — AQI Forecast API endpoint.

  POST /api/forecast  → run AQI forecast for a city/station
"""

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

router = APIRouter(prefix="/api/forecast", tags=["Forecast"])


# ---------------------------------------------------------------------------
# Request / Response schemas
# ---------------------------------------------------------------------------

class ForecastRequest(BaseModel):
    city_id:              str
    current_aqi:          int   = Field(..., ge=0, le=999)
    current_conditions:   dict  = Field(..., description="Dict with wind_speed, wind_direction, humidity, boundary_layer_height")
    weather_forecast_72h: list  = Field(..., description="List of 72 hourly weather dicts")
    station_id:           str   = Field(None, description="Optional station ID identifier")


# ---------------------------------------------------------------------------
# POST /api/forecast
# ---------------------------------------------------------------------------

@router.post("", summary="Run multi-step AQI forecast")
async def run_aqi_forecast(body: ForecastRequest, request: Request):
    """
    Generate predictions for hours [1, 3, 6, 12, 24, 48, 72] ahead.
    
    - Uses the recursive XGBoost forecast model if loaded.
    - Falls back to typical diurnal persistence baseline model if not.
    """
    cities = getattr(request.app.state, "cities", {})
    city   = cities.get(body.city_id)

    if not city:
        raise HTTPException(
            status_code=404,
            detail=f"City '{body.city_id}' not found. Available: {list(cities.keys())}",
        )

    # Import lazily to ensure clean imports and path injection
    from forecast_agent import run_forecast as _run

    result = await _run(
        current_aqi=body.current_aqi,
        current_conditions=body.current_conditions,
        weather_forecast_72h=body.weather_forecast_72h,
        city_config=city.model_dump() if hasattr(city, "model_dump") else city,
        station_id=body.station_id
    )

    return result
