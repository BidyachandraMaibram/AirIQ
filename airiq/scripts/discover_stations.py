"""
scripts/discover_stations.py
----------------------------
Fetches records from the CPCB live API to discover all unique station names for
Bengaluru, Delhi, Mumbai, and Kolkata, and print the schema fields.
Saves the raw JSON output to scripts/data/cpcb_station_discovery.json.
"""

import os
import sys
import json
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

async def discover():
    api_key = os.getenv("CPCB_API_KEY", "").strip()
    if not api_key:
        print("Error: CPCB_API_KEY is not set in airiq/backend/.env")
        return

    resource_id = "3b01bcb8-0b14-4abf-b6f2-c1bfd384ba69"
    url = f"https://api.data.gov.in/resource/{resource_id}"

    # Fetch 1000 records to inspect a large sample
    params = {
        "api-key": api_key,
        "format": "json",
        "limit": 1000
    }

    print(f"Fetching up to 1000 records from CPCB API...")
    async with httpx.AsyncClient(timeout=45) as client:
        try:
            resp = await client.get(url, params=params)
            if resp.status_code != 200:
                print(f"Failed to fetch data: HTTP {resp.status_code}")
                print(resp.text[:500])
                return

            payload = resp.json()
            records = payload.get("records", [])
            fields = payload.get("field", [])

            print(f"Successfully retrieved {len(records)} records.")

            # Save full raw output to scripts/data/cpcb_station_discovery.json
            data_dir = ROOT_DIR / "scripts" / "data"
            data_dir.mkdir(parents=True, exist_ok=True)
            output_file = data_dir / "cpcb_station_discovery.json"
            with open(output_file, "w", encoding="utf-8") as f:
                json.dump(payload, f, indent=2, ensure_ascii=False)
            print(f"Saved raw payload to: {output_file}")

            # Print field schema
            print("\n--- SAMPLE RECORD FIELDS ---")
            for f in fields:
                print(f"  • {f.get('id')} ({f.get('type')}): {f.get('name')}")

            # Sample record print
            if records:
                print("\n--- SAMPLE RECORD SAMPLE ---")
                for k, v in records[0].items():
                    print(f"  {k}: {v}")

            # Group unique stations by city (case-insensitive match)
            cities_data = {
                "bengaluru": set(),
                "mumbai": set(),
                "delhi": set(),
                "kolkata": set(),
                "others": set()
            }

            for r in records:
                station = r.get("station")
                city = (r.get("city") or "").lower()
                state = (r.get("state") or "").lower()
                
                if not station:
                    continue

                # Match city keywords
                if "bengaluru" in city or "bangalore" in city or "bengaluru" in station.lower():
                    cities_data["bengaluru"].add(station)
                elif "mumbai" in city or "bombay" in city or "mumbai" in station.lower():
                    cities_data["mumbai"].add(station)
                elif "delhi" in city or "delhi" in station.lower():
                    cities_data["delhi"].add(station)
                elif "kolkata" in city or "calcutta" in city or "kolkata" in station.lower():
                    cities_data["kolkata"].add(station)
                else:
                    cities_data["others"].add(f"{station} (City: {r.get('city')}, State: {r.get('state')})")

            # Print grouped unique stations
            for cname in ["bengaluru", "delhi", "mumbai", "kolkata"]:
                station_list = sorted(list(cities_data[cname]))
                print(f"\n==================== UNIQUE STATIONS IN {cname.upper()} ({len(station_list)}) ====================")
                for s in station_list:
                    print(f"  • \"{s}\"")

        except Exception as e:
            print(f"Error during discovery: {e}")

if __name__ == "__main__":
    asyncio.run(discover())
