"""
routers/cities.py — City configuration endpoints.

  GET /api/cities            → list of all cities (id + display_name)
  GET /api/cities/{city_id}  → full config for one city
"""

from fastapi import APIRouter, HTTPException, Request

router = APIRouter(prefix="/api/cities", tags=["Cities"])


@router.get("", summary="List all monitored cities")
async def list_cities(request: Request):
    """
    Returns a summary list of all cities loaded at startup.
    Use this to populate city selectors in the frontend.
    """
    cities: dict = getattr(request.app.state, "cities", {})
    return {
        "count":  len(cities),
        "cities": [
            {
                "city_id":      cfg.city_id,
                "display_name": cfg.display_name,
                "lat":          cfg.lat,
                "lon":          cfg.lon,
                "language":     cfg.language,
                "station_count": len(cfg.station_ids),
            }
            for cfg in cities.values()
        ],
    }


@router.get("/{city_id}", summary="Get full config for one city")
async def get_city(city_id: str, request: Request):
    """
    Returns the complete CityConfig (stations, wards, emission sources,
    vulnerable sites) for the requested city.
    """
    cities: dict = getattr(request.app.state, "cities", {})
    city = cities.get(city_id)
    if not city:
        raise HTTPException(
            status_code=404,
            detail=f"City '{city_id}' not found. Available: {list(cities.keys())}",
        )
    # Return as dict so Pydantic nested models serialise cleanly
    return city.model_dump()
