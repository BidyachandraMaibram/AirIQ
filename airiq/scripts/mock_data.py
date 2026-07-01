"""
scripts/mock_data.py
---------------------
Demo-day safety net.

Loads a complete, realistic mock dataset into Redis so the AirIQ demo
survives even if the CPCB API goes down during the presentation.

Usage:
  python scripts/mock_data.py --load    # write all mock data to Redis
  python scripts/mock_data.py --clear   # delete all airiq:* keys from Redis
  python scripts/mock_data.py --show    # print what's currently in Redis

Key naming matches the live pipeline exactly:
  health:{city_id}
  summary:{city_id}
  ward:{city_id}:{ward_id}
"""

import sys
import json
import asyncio
import argparse
from datetime import datetime, timezone
from pathlib import Path

# Make backend importable
ROOT_DIR = Path(__file__).resolve().parents[1]
BACKEND_DIR = ROOT_DIR / "backend"
AGENTS_DIR  = ROOT_DIR / "agents"
for p in [str(BACKEND_DIR), str(AGENTS_DIR)]:
    if p not in sys.path:
        sys.path.insert(0, p)

import redis.asyncio as aioredis
from config import settings

# ── Realistic station AQI values per city ─────────────────────────────────────
STATION_DATA = {
    "bengaluru": [
        {"station_id": "KARN001", "name": "Peenya",      "lat": 13.0295, "lon": 77.5132, "aqi": 62, "pm25": 17.5, "pm10": 35.2, "no2": 18.2},
        {"station_id": "KARN002", "name": "Silk Board",  "lat": 12.9172, "lon": 77.6228, "aqi": 56, "pm25": 14.8, "pm10": 28.5, "no2": 15.5},
        {"station_id": "KARN003", "name": "BTM Layout",  "lat": 12.9165, "lon": 77.6101, "aqi": 48, "pm25": 11.2, "pm10": 22.4, "no2": 12.1},
        {"station_id": "KARN004", "name": "Hebbal",      "lat": 13.0358, "lon": 77.5970, "aqi": 58, "pm25": 15.5, "pm10": 30.1, "no2": 16.7},
    ],
    "mumbai": [
        {"station_id": "MAHA001", "name": "Chembur",     "lat": 19.0626, "lon": 72.9005, "aqi": 68, "pm25": 20.2, "pm10": 39.8, "no2": 19.3},
        {"station_id": "MAHA002", "name": "Bandra",      "lat": 19.0596, "lon": 72.8295, "aqi": 52, "pm25": 12.4, "pm10": 25.3, "no2": 14.8},
        {"station_id": "MAHA003", "name": "Andheri",     "lat": 19.1136, "lon": 72.8697, "aqi": 56, "pm25": 14.7, "pm10": 28.6, "no2": 15.2},
        {"station_id": "MAHA004", "name": "Colaba",      "lat": 18.9067, "lon": 72.8147, "aqi": 44, "pm25": 10.4, "pm10": 19.1, "no2": 11.9},
    ],
    "delhi": [
        {"station_id": "DLHI001", "name": "Anand Vihar", "lat": 28.6469, "lon": 77.3162, "aqi": 135, "pm25": 48.4, "pm10": 85.6, "no2": 32.5},
        {"station_id": "DLHI002", "name": "ITO",         "lat": 28.6289, "lon": 77.2401, "aqi": 112, "pm25": 39.1, "pm10": 70.2, "no2": 28.4},
        {"station_id": "DLHI003", "name": "Lodhi Road",  "lat": 28.5931, "lon": 77.2218, "aqi": 92,  "pm25": 32.5, "pm10": 58.8, "no2": 22.1},
        {"station_id": "DLHI004", "name": "Punjabi Bagh","lat": 28.6733, "lon": 77.1313, "aqi": 125, "pm25": 44.7, "pm10": 80.3, "no2": 30.3},
    ],
    "kolkata": [
        {"station_id": "WBEN001", "name": "Howrah",      "lat": 22.5958, "lon": 88.2636, "aqi": 65, "pm25": 19.2, "pm10": 37.6, "no2": 18.1},
        {"station_id": "WBEN002", "name": "Ultadanga",   "lat": 22.5876, "lon": 88.3967, "aqi": 58, "pm25": 15.7, "pm10": 30.4, "no2": 15.8},
        {"station_id": "WBEN003", "name": "Salt Lake",   "lat": 22.5833, "lon": 88.4167, "aqi": 48, "pm25": 11.4, "pm10": 22.8, "no2": 12.6},
        {"station_id": "WBEN004", "name": "Dhapa",       "lat": 22.5688, "lon": 88.4294, "aqi": 75, "pm25": 23.3, "pm10": 45.5, "no2": 20.2},
    ],
}

# ── Ward AQI assignments (realistic, station-weighted) ─────────────────────────
WARD_DATA = {
    "bengaluru": [
        {"ward_id": "BLR_W01", "name": "Peenya Industrial",   "lat": 13.0295, "lon": 77.5132, "aqi": 62, "type": "industrial"},
        {"ward_id": "BLR_W02", "name": "Silk Board Junction", "lat": 12.9172, "lon": 77.6228, "aqi": 56, "type": "traffic"},
        {"ward_id": "BLR_W03", "name": "BTM Layout",          "lat": 12.9165, "lon": 77.6101, "aqi": 48, "type": "residential"},
        {"ward_id": "BLR_W04", "name": "Hebbal",              "lat": 13.0358, "lon": 77.5970, "aqi": 58, "type": "industrial"},
        {"ward_id": "BLR_W05", "name": "Koramangala",         "lat": 12.9352, "lon": 77.6245, "aqi": 44, "type": "mixed"},
        {"ward_id": "BLR_W06", "name": "Whitefield",          "lat": 12.9698, "lon": 77.7500, "aqi": 52, "type": "tech_park"},
    ],
    "mumbai": [
        {"ward_id": "MUM_W01", "name": "Chembur",    "lat": 19.0626, "lon": 72.9005, "aqi": 68, "type": "industrial"},
        {"ward_id": "MUM_W02", "name": "Bandra",     "lat": 19.0596, "lon": 72.8295, "aqi": 52, "type": "residential"},
        {"ward_id": "MUM_W03", "name": "Andheri",    "lat": 19.1136, "lon": 72.8697, "aqi": 56, "type": "mixed"},
        {"ward_id": "MUM_W04", "name": "Colaba",     "lat": 18.9067, "lon": 72.8147, "aqi": 44, "type": "coastal"},
        {"ward_id": "MUM_W05", "name": "Dharavi",    "lat": 19.0380, "lon": 72.8527, "aqi": 72, "type": "waste"},
        {"ward_id": "MUM_W06", "name": "Worli",      "lat": 18.9980, "lon": 72.8174, "aqi": 50, "type": "mixed"},
    ],
    "delhi": [
        {"ward_id": "DEL_W01", "name": "Anand Vihar",  "lat": 28.6469, "lon": 77.3162, "aqi": 135, "type": "traffic"},
        {"ward_id": "DEL_W02", "name": "ITO",          "lat": 28.6289, "lon": 77.2401, "aqi": 112, "type": "traffic"},
        {"ward_id": "DEL_W03", "name": "Lodhi Road",   "lat": 28.5931, "lon": 77.2218, "aqi": 92,  "type": "residential"},
        {"ward_id": "DEL_W04", "name": "Punjabi Bagh", "lat": 28.6733, "lon": 77.1313, "aqi": 125, "type": "industrial"},
        {"ward_id": "DEL_W05", "name": "Rohini",       "lat": 28.7495, "lon": 77.0680, "aqi": 118, "type": "residential"},
        {"ward_id": "DEL_W06", "name": "Connaught Pl", "lat": 28.6328, "lon": 77.2197, "aqi": 105, "type": "commercial"},
    ],
    "kolkata": [
        {"ward_id": "KOL_W01", "name": "Howrah",       "lat": 22.5958, "lon": 88.2636, "aqi": 65, "type": "industrial"},
        {"ward_id": "KOL_W02", "name": "Ultadanga",    "lat": 22.5876, "lon": 88.3967, "aqi": 58, "type": "mixed"},
        {"ward_id": "KOL_W03", "name": "Salt Lake",    "lat": 22.5833, "lon": 88.4167, "aqi": 48, "type": "residential"},
        {"ward_id": "KOL_W04", "name": "Dhapa",        "lat": 22.5688, "lon": 88.4294, "aqi": 75, "type": "waste_burning"},
        {"ward_id": "KOL_W05", "name": "Park Street",  "lat": 22.5526, "lon": 88.3527, "aqi": 52, "type": "commercial"},
        {"ward_id": "KOL_W06", "name": "Jadavpur",     "lat": 22.4993, "lon": 88.3693, "aqi": 54, "type": "residential"},
    ],
}

AQI_CATEGORIES = [
    (50,  "Good"),
    (100, "Satisfactory"),
    (200, "Moderate"),
    (300, "Poor"),
    (400, "Very Poor"),
    (500, "Severe"),
]

def get_aqi_category(aqi: int) -> str:
    for threshold, label in AQI_CATEGORIES:
        if aqi <= threshold:
            return label
    return "Severe"

def make_attribution(ward_type: str, aqi: int) -> dict:
    """Generate plausible attribution percentages based on ward type."""
    profiles = {
        "industrial":   {"industrial": 0.52, "traffic": 0.25, "construction": 0.10, "waste_burning": 0.08, "other": 0.05},
        "traffic":      {"traffic": 0.55, "industrial": 0.20, "construction": 0.12, "waste_burning": 0.08, "other": 0.05},
        "residential":  {"traffic": 0.35, "industrial": 0.25, "waste_burning": 0.20, "construction": 0.12, "other": 0.08},
        "waste":        {"waste_burning": 0.48, "industrial": 0.22, "traffic": 0.18, "construction": 0.07, "other": 0.05},
        "waste_burning":{"waste_burning": 0.50, "traffic": 0.20, "industrial": 0.15, "construction": 0.10, "other": 0.05},
        "mixed":        {"traffic": 0.30, "industrial": 0.30, "construction": 0.18, "waste_burning": 0.12, "other": 0.10},
        "coastal":      {"traffic": 0.30, "industrial": 0.25, "construction": 0.20, "waste_burning": 0.15, "other": 0.10},
        "commercial":   {"traffic": 0.45, "industrial": 0.20, "construction": 0.15, "waste_burning": 0.12, "other": 0.08},
        "tech_park":    {"traffic": 0.40, "construction": 0.25, "industrial": 0.18, "waste_burning": 0.10, "other": 0.07},
    }
    weights = profiles.get(ward_type, profiles["mixed"])
    top_sources = sorted(weights.items(), key=lambda x: -x[1])
    return {
        "dominant_source": top_sources[0][0],
        "confidence": 0.78 + (aqi / 5000),
        "sources": [
            {"type": k, "contribution": round(v * 100, 1), "intensity_modifier": round(v, 3)}
            for k, v in top_sources
        ],
        "method": "mock_fixture",
    }

ENFORCEMENT_TEMPLATES = {
    "industrial":    {"action": "Schedule unannounced stack emission inspection", "department": "KSPCB / State PCB"},
    "traffic":       {"action": "Deploy traffic management + diesel vehicle diversion", "department": "Traffic Police + RTO"},
    "construction":  {"action": "Issue dust suppression compliance order", "department": "BBMP / Municipal Corp"},
    "waste_burning": {"action": "Dispatch field team, issue burning prohibition notice", "department": "SWM Department"},
    "waste":         {"action": "Dispatch field team, issue burning prohibition notice", "department": "SWM Department"},
    "other":         {"action": "Investigate and identify source", "department": "Pollution Control Board"},
}

ADVISORY_LANGUAGES = {
    "bengaluru": ("kn", "kannada", "ಇಂದು ಗಾಳಿ ಗುಣಮಟ್ಟ ಕಳಪೆಯಾಗಿದೆ. ಹೊರಗೆ ಹೋಗುವಾಗ ಮಾಸ್ಕ್ ಧರಿಸಿ."),
    "mumbai":    ("mr", "marathi",  "आज हवेची गुणवत्ता खराब आहे. बाहेर जाताना मास्क घाला."),
    "delhi":     ("hi", "hindi",    "आज वायु गुणवत्ता बहुत खराब है। बाहर जाते समय मास्क पहनें।"),
    "kolkata":   ("bn", "bengali",  "আজ বায়ু মান খারাপ। বাইরে যাওয়ার সময় মাস্ক পরুন।"),
}

def make_forecast(current_aqi: int) -> dict:
    import math
    hours = [1, 3, 6, 12, 24]
    forecasts = []
    for h in hours:
        # Realistic decay/rise pattern
        factor = 1 + 0.05 * math.sin(h * 0.5) - 0.003 * h
        predicted = max(30, int(current_aqi * factor))
        forecasts.append({
            "hours_ahead": h,
            "predicted_aqi": predicted,
            "category": get_aqi_category(predicted),
            "confidence": round(max(0.5, 0.92 - h * 0.008), 3),
        })
    return {
        "forecast": forecasts,
        "peak_24h": max(f["predicted_aqi"] for f in forecasts),
        "model": "mock_xgboost_fixture",
        "rmse": 18.4,
    }

def make_ward_blob(city_id: str, ward: dict) -> dict:
    now = datetime.now(timezone.utc).isoformat()
    aqi = ward["aqi"]
    ward_type = ward.get("type", "mixed")
    attribution = make_attribution(ward_type, aqi)
    dominant = attribution["dominant_source"]
    enforcement_tpl = ENFORCEMENT_TEMPLATES.get(dominant, ENFORCEMENT_TEMPLATES["other"])

    lang_code, lang_name, advisory_text = ADVISORY_LANGUAGES[city_id]

    enforcement = {
        "recommendations": [{
            "rank": 1,
            "source_type": dominant,
            "contribution_pct": attribution["sources"][0]["contribution"],
            "action": enforcement_tpl["action"],
            "department": enforcement_tpl["department"],
            "priority": "HIGH" if aqi >= 200 else "MEDIUM",
            "aqi_context": aqi,
        }],
        "summary": f"Primary source: {dominant}. {enforcement_tpl['action']}.",
    }

    advisory = {
        "language": lang_name,
        "language_code": lang_code,
        "general_public": advisory_text,
        "sensitive_groups": advisory_text + " संवेदनशील समूह: इस वायु में बाहर न जाएं।" if lang_code == "hi" else advisory_text,
        "outdoor_workers": advisory_text,
        "aqi_level": get_aqi_category(aqi),
        "generated_by": "mock_fixture",
    }

    incident_report = None
    if get_aqi_category(aqi) in ["Very Poor", "Severe"]:
        incident_report = {
            "severity": "CRITICAL" if aqi >= 300 else "HIGH",
            "headline": f"AQI {aqi} — {get_aqi_category(aqi)} air quality in {ward['name']}",
            "immediate_actions": [
                "Activate emergency protocol",
                "Alert vulnerable population",
                f"Contact {enforcement_tpl['department']}",
            ],
            "generated_by": "mock_fixture",
        }

    return {
        "aqi": aqi,
        "category": get_aqi_category(aqi),
        "attribution": attribution,
        "enforcement": enforcement,
        "forecast": make_forecast(aqi),
        "advisory": advisory,
        "incident_report": incident_report,
        "updated_at": now,
        "source": "mock_fixture",
    }

def make_health_blob(city_id: str) -> dict:
    stations = STATION_DATA[city_id]
    stations_health = {
        s["station_id"]: {
            "quality_score": 0.95,
            "flags": [],
            "aqi": s["aqi"],
            "name": s["name"],
        }
        for s in stations
    }
    return {
        "last_fetch": datetime.now(timezone.utc).isoformat(),
        "station_count": len(stations),
        "stations": stations_health,
        "source": "mock_fixture",
    }

def make_summary_blob(city_id: str) -> dict:
    city_names = {
        "bengaluru": "Bengaluru",
        "mumbai":    "Mumbai",
        "delhi":     "Delhi",
        "kolkata":   "Kolkata",
    }
    wards = WARD_DATA[city_id]
    return {
        "city_id": city_id,
        "name": city_names[city_id],
        "wards": [
            {
                "ward_id":  w["ward_id"],
                "name":     w["name"],
                "aqi":      w["aqi"],
                "category": get_aqi_category(w["aqi"]),
                "lat":      w["lat"],
                "lon":      w["lon"],
            }
            for w in wards
        ],
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "source": "mock_fixture",
    }


# ── Redis helpers ──────────────────────────────────────────────────────────────

async def get_redis() -> aioredis.Redis:
    client = aioredis.from_url(
        settings.redis_url,
        encoding="utf-8",
        decode_responses=True,
        socket_connect_timeout=3,
    )
    await client.ping()
    return client


async def load_all(verbose: bool = True) -> None:
    r = await get_redis()
    TTL = 7200  # 2-hour TTL for demo fixtures

    total_keys = 0
    for city_id in ["bengaluru", "delhi", "mumbai", "kolkata"]:
        # health key
        health_key = f"health:{city_id}"
        await r.setex(health_key, TTL, json.dumps(make_health_blob(city_id), default=str))
        if verbose:
            print(f"  [WRITE] {health_key}")

        # summary key
        summary_key = f"summary:{city_id}"
        await r.setex(summary_key, TTL, json.dumps(make_summary_blob(city_id), default=str))
        if verbose:
            print(f"  [WRITE] {summary_key}")

        # ward keys
        for ward in WARD_DATA[city_id]:
            ward_key = f"ward:{city_id}:{ward['ward_id']}"
            blob = make_ward_blob(city_id, ward)
            await r.setex(ward_key, TTL, json.dumps(blob, default=str))
            if verbose:
                print(f"  [WRITE] {ward_key}  (AQI={ward['aqi']}, {get_aqi_category(ward['aqi'])})")
            total_keys += 1

        total_keys += 2  # health + summary
        print(f"  [OK] {city_id}: {2 + len(WARD_DATA[city_id])} keys written")

    await r.aclose()
    print(f"\nDone. {total_keys} total Redis keys loaded with 2-hour TTL.")
    print("The demo is armed. CPCB API downtime will NOT affect the presentation.")


async def clear_all(verbose: bool = True) -> None:
    r = await get_redis()
    patterns = ["health:*", "summary:*", "ward:*"]
    total_deleted = 0
    for pattern in patterns:
        keys = await r.keys(pattern)
        if keys:
            deleted = await r.delete(*keys)
            total_deleted += deleted
            if verbose:
                print(f"  Deleted {deleted} keys matching '{pattern}'")
    await r.aclose()
    print(f"\nCleared {total_deleted} AirIQ keys from Redis.")


async def show_all() -> None:
    r = await get_redis()
    for pattern in ["health:*", "summary:*", "ward:*"]:
        keys = sorted(await r.keys(pattern))
        print(f"\n-- {pattern} ({len(keys)} keys) --")
        for k in keys:
            ttl = await r.ttl(k)
            print(f"  {k}  [TTL={ttl}s]")
    await r.aclose()


# ── CLI entry point ────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="AirIQ demo mock data manager")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--load",  action="store_true", help="Load mock data into Redis")
    group.add_argument("--clear", action="store_true", help="Clear all AirIQ Redis keys")
    group.add_argument("--show",  action="store_true", help="List all AirIQ keys in Redis")
    args = parser.parse_args()

    if args.load:
        print("\nLoading demo mock data into Redis...\n")
        asyncio.run(load_all())
    elif args.clear:
        print("\nClearing all AirIQ Redis keys...\n")
        asyncio.run(clear_all())
    elif args.show:
        asyncio.run(show_all())
