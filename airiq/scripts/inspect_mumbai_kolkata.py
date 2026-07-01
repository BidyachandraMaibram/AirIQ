"""
scripts/inspect_mumbai_kolkata.py
---------------------------------
Inspects all station names under Mumbai/Bombay and Kolkata/Calcutta on data.gov.in.
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

async def test_cities():
    api_key = os.getenv("CPCB_API_KEY", "").strip()
    if not api_key:
        print("❌ CPCB_API_KEY is not set.")
        return

    resource_id = "3b01bcb8-0b14-4abf-b6f2-c1bfd384ba69"
    url = f"https://api.data.gov.in/resource/{resource_id}"

    cities = ["Mumbai", "Kolkata", "Delhi"]

    async with httpx.AsyncClient(timeout=30) as client:
        for city in cities:
            params = {
                "api-key": api_key,
                "format": "json",
                "limit": 400,
                "filters[city]": city
            }
            try:
                resp = await client.get(url, params=params)
                if resp.status_code != 200:
                    print(f"Failed {city}: {resp.status_code}")
                    continue

                data = resp.json()
                records = data.get("records", [])
                
                stations = set()
                for r in records:
                    stations.add(r.get("station"))

                print(f"\nFound {len(stations)} stations under city='{city}':")
                for st in sorted(stations):
                    print(f"  • \"{st}\"")

            except Exception as e:
                print(f"Error {city}: {e}")

if __name__ == "__main__":
    asyncio.run(test_cities())
