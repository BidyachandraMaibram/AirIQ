"""
scripts/test_cpcb_live.py
-------------------------
Tests the live CPCB/data.gov.in API connection using the key in backend/.env.
Queries the CPCB resource and prints the raw response schema.
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

# Load the env file specifically from the backend directory
env_path = BACKEND_DIR / ".env"
load_dotenv(dotenv_path=env_path)

async def test_live_cpcb():
    api_key = os.getenv("CPCB_API_KEY", "").strip()
    if not api_key:
        print("❌ CPCB_API_KEY is not set in airiq/backend/.env.")
        print("Please register at https://data.gov.in/, get your API key, and add it to the .env file.")
        return

    print(f"Using API Key: {api_key[:6]}...{api_key[-4:] if len(api_key) > 10 else ''}")
    resource_id = "3b01bcb8-0b14-4abf-b6f2-c1bfd384ba69"
    url = f"https://api.data.gov.in/resource/{resource_id}"

    # Query with no filters first to see fields and station naming format
    params = {
        "api-key": api_key,
        "format": "json",
        "limit": 3
    }

    print(f"Fetching from: {url}")
    async with httpx.AsyncClient(timeout=30) as client:
        try:
            resp = await client.get(url, params=params)
            print(f"Status Code: {resp.status_code}")
            if resp.status_code != 200:
                print("Response text:", resp.text)
                return

            data = resp.json()
            fields = data.get("field", [])
            print("\n--- API FIELDS SCHEMA ---")
            for f in fields:
                print(f"  • {f.get('id')} ({f.get('type')}): {f.get('name')}")

            records = data.get("records", [])
            print(f"\n--- SAMPLE RECORDS (Count: {len(records)}) ---")
            for i, r in enumerate(records):
                print(f"\nRecord {i+1}:")
                for k, v in r.items():
                    print(f"  {k}: {v}")

        except Exception as e:
            import traceback
            print(f"[ERROR] Error during request: {e}")
            traceback.print_exc()

if __name__ == "__main__":
    asyncio.run(test_live_cpcb())
