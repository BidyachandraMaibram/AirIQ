"""
scripts/verify_cities.py
-------------------------
End-to-end integration verifier for AirIQ.

Checks:
  1. All 4 city JSON configs load and pass Pydantic validation
  2. CPCB mock/live fallback returns records for each city
  3. Open-Meteo returns weather for each city lat/lon
  4. All API endpoints return the expected response shapes
  5. Advisory language code matches city config

Run with:
  cd airiq/backend
  python ../scripts/verify_cities.py
"""

import sys
import asyncio
import json
from pathlib import Path

# Adjust PYTHONPATH so we can import backend modules
ROOT_DIR = Path(__file__).resolve().parents[1]
BACKEND_DIR = ROOT_DIR / "backend"
AGENTS_DIR  = ROOT_DIR / "agents"
for p in [str(BACKEND_DIR), str(AGENTS_DIR)]:
    if p not in sys.path:
        sys.path.insert(0, p)

import httpx
from city_loader import load_all_cities
from models.city import CityConfig
from ingestion.cpcb_fetcher import fetch_city_aqi, mock_city_aqi
from ingestion.weather_fetcher import fetch_city_weather

API_BASE = "http://localhost:8000"

# Expected language codes per city
EXPECTED_LANG = {
    "bengaluru": "kn",
    "mumbai":    "mr",
    "delhi":     "hi",
    "kolkata":   "bn",
}

# Result tracking
results: dict[str, dict] = {}


# ── Helpers ───────────────────────────────────────────────────────────────────

def mark(city_id: str, key: str, ok: bool, note: str = ""):
    results.setdefault(city_id, {})[key] = (ok, note)


def print_table():
    COLS = ["Config", "API Data", "Weather", "Ward Detail", "Language"]
    WIDTH = 14

    header = f"{'City':<12}" + "".join(f"{c:^{WIDTH}}" for c in COLS)
    print("\n" + "=" * len(header))
    print(header)
    print("-" * len(header))

    for city_id, checks in sorted(results.items()):
        row = f"{city_id:<12}"
        for col in COLS:
            key = col.lower().replace(" ", "_")
            ok, note = checks.get(key, (None, "—"))
            symbol = "✅" if ok else ("❌" if ok is False else "⏭")
            cell = f"{symbol} {note[:6] if note else ''}"
            row += f"{cell:^{WIDTH}}"
        print(row)

    print("=" * len(header))

    # Summary
    fails = [(cid, k, note) for cid, checks in results.items()
             for k, (ok, note) in checks.items() if ok is False]
    if fails:
        print(f"\n🔴  {len(fails)} check(s) FAILED:")
        for cid, k, note in fails:
            print(f"   • {cid} / {k}: {note}")
    else:
        print("\n🟢  All checks PASSED — AirIQ is healthy!\n")


# ── Check 1: Config validation ─────────────────────────────────────────────────

def check_configs(cities: dict) -> None:
    print("\n[1/5] Validating city JSON configs…")
    for city_id, cfg in cities.items():
        try:
            assert isinstance(cfg, CityConfig), "Not a CityConfig instance"
            assert cfg.city_id == city_id
            assert len(cfg.station_ids) > 0, "No station IDs"
            assert len(cfg.wards) >= 6, f"Only {len(cfg.wards)} wards (need ≥6)"
            assert len(cfg.emission_sources) >= 4, f"Only {len(cfg.emission_sources)} sources"
            assert len(cfg.vulnerable_sites) >= 3, f"Only {len(cfg.vulnerable_sites)} vulnerable sites"
            mark(city_id, "config", True, "ok")
            print(f"   ✅ {city_id}: {len(cfg.wards)} wards, {len(cfg.emission_sources)} sources")
        except Exception as exc:
            mark(city_id, "config", False, str(exc)[:60])
            print(f"   ❌ {city_id}: {exc}")


# ── Check 2: AQI data (CPCB mock) ──────────────────────────────────────────────

async def check_aqi_data(cities: dict) -> None:
    print("\n[2/5] Testing AQI data fetch (CPCB or mock fallback)…")
    for city_id, cfg in cities.items():
        try:
            cfg_dict = cfg.model_dump()
            records = await fetch_city_aqi(cfg_dict)
            if not records:
                records = mock_city_aqi(cfg_dict)

            assert records, "No records returned even from mock"
            assert all("aqi" in r for r in records), "Some records missing 'aqi'"
            mark(city_id, "api_data", True, f"{len(records)}rec")
            print(f"   ✅ {city_id}: {len(records)} station records")
        except Exception as exc:
            mark(city_id, "api_data", False, str(exc)[:60])
            print(f"   ❌ {city_id}: {exc}")


# ── Check 3: Weather (Open-Meteo) ───────────────────────────────────────────────

async def check_weather(cities: dict) -> None:
    print("\n[3/5] Testing Open-Meteo weather fetch…")
    for city_id, cfg in cities.items():
        try:
            cfg_dict = cfg.model_dump()
            weather = await fetch_city_weather(cfg_dict)
            assert weather, "Empty weather response"
            current = weather.get("current", {})
            assert "wind_speed" in current, "Missing wind_speed in current"
            forecast = weather.get("forecast_72h", [])
            assert len(forecast) >= 24, f"Only {len(forecast)} forecast hours"
            mark(city_id, "weather", True, f"{len(forecast)}h")
            print(f"   ✅ {city_id}: wind {current['wind_speed']:.1f} km/h, {len(forecast)} forecast hours")
        except Exception as exc:
            mark(city_id, "weather", False, str(exc)[:60])
            print(f"   ❌ {city_id}: {exc}")


# ── Check 4: API endpoints ──────────────────────────────────────────────────────

async def check_api_endpoints(cities: dict) -> None:
    print("\n[4/5] Testing live API endpoints at", API_BASE)
    async with httpx.AsyncClient(timeout=15) as client:

        # 4a. GET /api/cities
        try:
            r = await client.get(f"{API_BASE}/api/cities")
            r.raise_for_status()
            raw = r.json()
            city_list = raw if isinstance(raw, list) else raw.get("cities", [])
            ids = {c["city_id"] for c in city_list}
            missing = set(cities.keys()) - ids
            assert not missing, f"Missing cities: {missing}"
            print(f"   ✅ GET /api/cities → {ids}")
        except Exception as exc:
            print(f"   ❌ GET /api/cities: {exc}")
            # mark all cities as failed for this sub-check
            for cid in cities:
                mark(cid, "ward_detail", False, "cities endpoint failed")
            return

        # 4b. Per-city summary + ward detail
        for city_id, cfg in cities.items():
            # Summary
            try:
                r = await client.get(f"{API_BASE}/api/city/{city_id}/summary")
                r.raise_for_status()
                summary = r.json()
                wards = summary.get("wards", [])
                assert len(wards) > 0, "Empty wards array"
                print(f"   ✅ GET /api/city/{city_id}/summary → {len(wards)} wards")
            except Exception as exc:
                mark(city_id, "ward_detail", False, f"summary: {exc}"[:60])
                print(f"   ❌ GET /api/city/{city_id}/summary: {exc}")
                continue

            # Ward detail (first ward)
            try:
                first_ward_id = cfg.wards[0].ward_id
                r = await client.get(f"{API_BASE}/api/ward/{city_id}/{first_ward_id}")
                r.raise_for_status()
                ward = r.json()
                required = ["aqi", "attribution", "enforcement", "forecast", "advisory"]
                missing_keys = [k for k in required if k not in ward]
                assert not missing_keys, f"Missing keys: {missing_keys}"
                mark(city_id, "ward_detail", True, "ok")
                print(f"   ✅ GET /api/ward/{city_id}/{first_ward_id} → AQI={ward['aqi']}, category={ward['category']}")
            except Exception as exc:
                mark(city_id, "ward_detail", False, str(exc)[:60])
                print(f"   ❌ GET /api/ward/{city_id}/{first_ward_id}: {exc}")


# ── Check 5: Advisory language ──────────────────────────────────────────────────

async def check_language(cities: dict) -> None:
    print("\n[5/5] Checking advisory language codes…")
    async with httpx.AsyncClient(timeout=15) as client:
        for city_id, cfg in cities.items():
            expected = EXPECTED_LANG.get(city_id, "en")
            try:
                first_ward_id = cfg.wards[0].ward_id
                r = await client.get(f"{API_BASE}/api/ward/{city_id}/{first_ward_id}")
                r.raise_for_status()
                ward = r.json()
                advisory = ward.get("advisory", {})
                lang = advisory.get("language", "").lower()

                # Map language name to code for comparison
                lang_map = {
                    "kannada": "kn", "marathi": "mr",
                    "hindi": "hi", "bengali": "bn", "english": "en"
                }
                lang_code = lang_map.get(lang, lang)  # already a code or map it

                assert lang_code == expected, f"Got '{lang}' (code '{lang_code}'), expected '{expected}'"
                mark(city_id, "language", True, lang_code)
                print(f"   ✅ {city_id}: advisory language = {lang} ({lang_code})")
            except Exception as exc:
                mark(city_id, "language", False, str(exc)[:60])
                print(f"   ❌ {city_id}: {exc}")


# ── Main entry ─────────────────────────────────────────────────────────────────

async def main():
    print("=" * 60)
    print("  AirIQ — End-to-End Verification Script")
    print("=" * 60)

    # Initialise result stubs for all expected cities
    for cid in EXPECTED_LANG:
        results[cid] = {}

    # Load city configs
    print("\nLoading city configurations…")
    cities = load_all_cities()
    if not cities:
        print("❌ No city configs found in configs/. Aborting.")
        sys.exit(1)
    print(f"   Found {len(cities)} configs: {list(cities.keys())}")

    check_configs(cities)
    await check_aqi_data(cities)
    await check_weather(cities)
    await check_api_endpoints(cities)
    await check_language(cities)

    print_table()


if __name__ == "__main__":
    asyncio.run(main())
