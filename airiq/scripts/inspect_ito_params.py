"""
scripts/inspect_ito_params.py
----------------------------
Queries the CPCB API for all records of a single station to inspect all parameters.
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

async def inspect_params():
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
            "limit": 100,
            "filters[station]": "ITO, Delhi - CPCB"
        }
        try:
            resp = await client.get(url, params=params)
            if resp.status_code != 200:
                print(f"Failed to fetch: {resp.status_code}")
                return

            data = resp.json()
            records = data.get("records", [])
            print(f"Found {len(records)} records for ITO, Delhi:")
            for r in records:
                print(f"  • Parameter: {r.get('parameter')} = {r.get('value')} {r.get('unit')} (Last update: {r.get('last_update')})")

        except Exception as e:
            print(f"Error: {e}")

if __name__ == "__main__":
    asyncio.run(inspect_params())
