"""
backend/city_loader.py
-----------------------
Loads and validates all city configuration JSON files at startup.

Usage (in main.py lifespan):
    from city_loader import load_all_cities
    app.state.cities = load_all_cities()
"""

import json
import logging
from pathlib import Path

from pydantic import ValidationError

from models.city import CityConfig

logger = logging.getLogger("airiq.city_loader")

# Path to the configs/ directory (two levels up from backend/)
_CONFIGS_DIR = Path(__file__).resolve().parents[1] / "configs"


def load_all_cities() -> dict[str, CityConfig]:
    """
    Read every *.json file in configs/, validate against CityConfig,
    and return a dict keyed by city_id.

    Invalid or unreadable files are skipped with a warning — they do not
    crash startup.
    """
    cities: dict[str, CityConfig] = {}

    json_files = sorted(_CONFIGS_DIR.glob("*.json"))
    if not json_files:
        logger.warning("No city JSON files found in %s", _CONFIGS_DIR)
        return cities

    for path in json_files:
        # Skip placeholder / non-city files
        if path.stem.startswith("_"):
            continue

        try:
            with open(path, encoding="utf-8") as f:
                raw = json.load(f)

            config = CityConfig(**raw)
            cities[config.city_id] = config
            logger.info(
                "Loaded city %-12s — %d stations, %d sources, %d wards, %d sites",
                config.city_id,
                len(config.station_ids),
                len(config.emission_sources),
                len(config.wards),
                len(config.vulnerable_sites),
            )

        except json.JSONDecodeError as exc:
            logger.warning("Skipping %s — JSON parse error: %s", path.name, exc)
        except ValidationError as exc:
            logger.warning("Skipping %s — validation error:\n%s", path.name, exc)
        except Exception as exc:
            logger.warning("Skipping %s — unexpected error: %s", path.name, exc)

    logger.info("City loader complete — %d cities loaded: %s",
                len(cities), list(cities.keys()))
    return cities


def get_city(city_id: str, app_state) -> CityConfig | None:
    """Convenience helper to fetch a CityConfig from app.state.cities."""
    return getattr(app_state, "cities", {}).get(city_id)
