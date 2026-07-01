"""
main.py — AirIQ FastAPI application entry point.

Responsibilities:
  - Define the app with a lifespan context manager (startup/shutdown).
  - Wire up Redis, APScheduler, city loader, CORS, and all routers.
"""

import sys
import asyncio
from contextlib import asynccontextmanager
from pathlib import Path
import logging

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

# Make the agents/ directory importable from anywhere in the app
_AGENTS_DIR = str(Path(__file__).resolve().parents[1] / "agents")
if _AGENTS_DIR not in sys.path:
    sys.path.insert(0, _AGENTS_DIR)

from config import settings
from cache import init_redis, close_redis
from scheduler import start_scheduler, stop_scheduler
from city_loader import load_all_cities
from routers import health
from routers import cities
from routers import attribution
from routers import forecast
from routers import advisory
from routers import incident
from routers import api

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s — %(message)s",
)
logger = logging.getLogger("airiq")


# ---------------------------------------------------------------------------
# Lifespan — replaces deprecated @app.on_event("startup/shutdown")
# ---------------------------------------------------------------------------
@asynccontextmanager
async def lifespan(app: FastAPI):
    """Manage startup and shutdown of shared resources."""
    # ── Startup ──────────────────────────────────────────────────────────
    logger.info("AirIQ starting up…")

    # Load and validate all city configs from configs/*.json
    app.state.cities = load_all_cities()
    logger.info("Loaded %d city configs: %s",
                len(app.state.cities), list(app.state.cities.keys()))

    await init_redis()          # connect Redis (falls back to in-memory)
    await start_scheduler()     # kick off APScheduler async scheduler

    # Trigger city pipelines immediately in the background so API is warm
    from scheduler import run_city_pipeline
    for city_cfg in app.state.cities.values():
        cfg_dict = city_cfg.model_dump() if hasattr(city_cfg, "model_dump") else city_cfg
        asyncio.create_task(run_city_pipeline(cfg_dict))

    logger.info("AirIQ ready ✔")
    yield  # application runs here

    # ── Shutdown ─────────────────────────────────────────────────────────
    logger.info("AirIQ shutting down…")
    await stop_scheduler()
    await close_redis()
    logger.info("AirIQ stopped cleanly.")


# ---------------------------------------------------------------------------
# App instantiation
# ---------------------------------------------------------------------------
app = FastAPI(
    title="AirIQ",
    description="AI-powered urban air quality intelligence platform for Indian cities.",
    version="0.1.0",
    lifespan=lifespan,
)

# ---------------------------------------------------------------------------
# CORS — wide-open for hackathon; tighten before production
# ---------------------------------------------------------------------------
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------------------------------------------------------------------------
# Routers
# ---------------------------------------------------------------------------
app.include_router(health.router, tags=["Health"])
app.include_router(cities.router)           # GET /api/cities, GET /api/cities/{id}
app.include_router(attribution.router)      # POST /api/attribution
app.include_router(forecast.router)         # POST /api/forecast
app.include_router(advisory.router)         # GET /api/advisory
app.include_router(incident.router)         # GET /api/incident
app.include_router(api.router)              # GET /api/... (Pre-cached fast paths)


# ---------------------------------------------------------------------------
# Debug endpoint — remove before production
# ---------------------------------------------------------------------------
@app.get("/api/debug/cities", tags=["Debug"],
         summary="[DEV ONLY] Return full CityConfig for all cities")
async def debug_cities(request: Request):
    """Returns raw CityConfig dicts — useful during demo prep and integration tests."""
    cities_map = getattr(request.app.state, "cities", {})
    return JSONResponse({
        cid: cfg.model_dump() for cid, cfg in cities_map.items()
    })
