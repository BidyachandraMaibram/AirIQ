"""
scripts/inspect_cpcb_cities.py
------------------------------
Queries the CPCB API for stations in Bengaluru, Delhi, Mumbai, and Kolkata,
and prints their actual names so we can map them correctly in our configs.
"""

import os
import sys
import asyncio
from pathlib import Path

# Adjust path to import config
ROOT_DIR = Path(__file__).resolve().parents[1]
BACKEND_DIR = ROOT_DIR / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

import httpx
from dotenv import load_dotenv

# Load env file
env_path = BACKEND_DIR / ".env"
load_dotenv(dotenv_path=env_path)

async def inspect_cities():
    api_key = os.getenv("CPCB_API_KEY", "").strip()
    if not api_key:
        print("❌ CPCB_API_KEY is not set.")
        return

    resource_id = "3b01bcb8-0b14-4abf-b6f2-c1bfd384ba69"
    url = f"https://api.data.gov.in/resource/{resource_id}"

    cities_to_check = ["Bengaluru", "Delhi", "Mumbai", "Kolkata"]

    async with httpx.AsyncClient(timeout=30) as client:
        for city in cities_to_check:
            print(f"\n==================== {city.upper()} STATIONS ====================")
            params = {
                "api-key": api_key,
                "format": "json",
                "limit": 200,
                "filters[city]": city
            }
            try:
                resp = await client.get(url, params=params)
                if resp.status_code != 200:
                    print(f"Failed to fetch for {city}: {resp.status_code}")
                    continue

                data = resp.json()
                records = data.get("records", [])
                
                # Get unique stations
                stations = {}
                for r in records:
                    st_name = r.get("station")
                    param = r.get("parameter")
                    val = r.get("value")
                    last_update = r.get("last_update")
                    if st_name not in stations:
                        stations[st_name] = []
                    stations[st_name].append((param, val))

                print(f"Found {len(stations)} monitoring stations in {city}:")
                for st, params_list in stations.items():
                    print(f"\n  • Station Name: \"{st}\"")
                    params_str = ", ".join([f"{p}: {v}" for p, v in params_list[:4]])
                    print(f"    Sample values: {params_str}")

            except Exception as e:
                print(f"Error inspecting {city}: {e}")

if __name__ == "__main__":
    asyncio.run(inspect_cities())
