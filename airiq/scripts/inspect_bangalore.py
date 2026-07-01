"""
scripts/inspect_bangalore.py
----------------------------
Checks if there are stations under the city name "Bangalore" on data.gov.in.
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

async def test_bangalore():
    api_key = os.getenv("CPCB_API_KEY", "").strip()
    if not api_key:
        print("❌ CPCB_API_KEY is not set.")
        return

    resource_id = "3b01bcb8-0b14-4abf-b6f2-c1bfd384ba69"
    url = f"https://api.data.gov.in/resource/{resource_id}"

    async with httpx.AsyncClient(timeout=30) as client:
        params = {
            "api-key": api_key,
            "format": "json",
            "limit": 200,
            "filters[city]": "Bangalore"
        }
        try:
            resp = await client.get(url, params=params)
            if resp.status_code != 200:
                print(f"Failed: {resp.status_code}")
                return

            data = resp.json()
            records = data.get("records", [])
            
            stations = set()
            for r in records:
                stations.add(r.get("station"))

            print(f"Found {len(stations)} stations under city='Bangalore':")
            for st in sorted(stations):
                print(f"  • \"{st}\"")

        except Exception as e:
            print(f"Error: {e}")

if __name__ == "__main__":
    asyncio.run(test_bangalore())
