"""
backend/models/city.py
-----------------------
Pydantic models for city configuration.

Every city JSON in configs/ is validated against CityConfig at startup.
These models are the single source of truth for city data structure.
"""

from typing import Literal
from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Sub-models
# ---------------------------------------------------------------------------

class EmissionSource(BaseModel):
    """A known pollution emission hotspot within a city."""
    name:      str
    lat:       float
    lon:       float
    type:      Literal["industrial", "traffic", "construction", "waste_burning", "other"]
    intensity: float = Field(..., ge=0.0, le=1.0,
                             description="Relative emission strength (0=negligible, 1=maximum)")


class Ward(BaseModel):
    """An administrative ward / neighbourhood used for population-weighted analysis."""
    ward_id:    str
    name:       str
    lat:        float
    lon:        float
    population: int = Field(..., ge=0)


class VulnerableSite(BaseModel):
    """A site where high AQI has elevated public-health impact."""
    name: str
    type: Literal["hospital", "school", "outdoor_workers"]
    lat:  float
    lon:  float


class Station(BaseModel):
    """Optional per-station metadata (name, exact location)."""
    name: str
    lat:  float
    lon:  float
    cpcb_station_name: str | None = None


# ---------------------------------------------------------------------------
# Root model
# ---------------------------------------------------------------------------

class CityConfig(BaseModel):
    """Complete configuration for one monitored city."""

    # ── Identity ─────────────────────────────────────────────────────────────
    city_id:      str   # machine-readable slug, e.g. "bengaluru"
    name:         str   # short English name, e.g. "Bengaluru"
    display_name: str   # human-facing label, e.g. "Bengaluru, Karnataka"

    # ── Geography ────────────────────────────────────────────────────────────
    lat: float
    lon: float

    # ── Localisation ─────────────────────────────────────────────────────────
    language: Literal["kn", "mr", "hi", "bn", "en"]
    timezone: str  # e.g. "Asia/Kolkata"

    # ── AQI Stations ─────────────────────────────────────────────────────────
    station_ids: list[str]
    stations:    dict[str, Station] = Field(default_factory=dict,
                                            description="Optional per-station metadata keyed by station_id")

    # ── CPCB API ─────────────────────────────────────────────────────────────
    cpcb_resource_id: str = "3b01bcb8-0b14-4abf-b6f2-c1bfd384ba69"

    # ── Emission sources for attribution agent ───────────────────────────────
    emission_sources: list[EmissionSource] = Field(default_factory=list)

    # ── Wards for population-weighted analysis ───────────────────────────────
    wards: list[Ward] = Field(default_factory=list)

    # ── Vulnerable sites for advisory agent ─────────────────────────────────
    vulnerable_sites: list[VulnerableSite] = Field(default_factory=list)

    # ── AQI breakpoints (CPCB India standard) ────────────────────────────────
    aqi_thresholds: dict[str, int] = Field(
        default={
            "good": 50,
            "satisfactory": 100,
            "moderate": 200,
            "poor": 300,
            "very_poor": 400,
            "severe": 500,
        }
    )
