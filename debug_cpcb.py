#!/usr/bin/env python3
"""Debug script to check what's happening with CPCB fetching."""

import asyncio
import logging
import sys
from pathlib import Path

# Enable debug logging
logging.basicConfig(level=logging.DEBUG)

# Add backend to path
backend_dir = Path(__file__).parent / "airiq" / "backend"
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

from ingestion.cpcb_fetcher import fetch_city_aqi

# Test with Delhi config - using the exact structure from the config file
delhi_config = {
    "city_id": "delhi",
    "name": "Delhi",
    "lat": 28.6139,
    "lon": 77.2090,
    "station_ids": [
        "Anand Vihar, Delhi - DPCC",
        "R K Puram, Delhi - DPCC",
        "Dwarka-Sector 8, Delhi - DPCC",
        "Rohini, Delhi - DPCC",
        "Okhla Phase-2, Delhi - DPCC",
        "Punjabi Bagh, Delhi - DPCC",
        "Wazirpur, Delhi - DPCC"
    ],
    "stations": {
        "Anand Vihar, Delhi - DPCC": {"name": "Anand Vihar", "lat": 28.6469, "lon": 28.6469},
        "R K Puram, Delhi - DPCC": {"name": "R K Puram", "lat": 28.5638, "lon": 77.1746},
        "Dwarka-Sector 8, Delhi - DPCC": {"name": "Dwarka", "lat": 28.5921, "lon": 77.0460},
        "Rohini, Delhi - DPCC": {"name": "Rohini", "lat": 28.7041, "lon": 77.1025},
        "Okhla Phase-2, Delhi - DPCC": {"name": "Okhla", "lat": 28.5311, "lon": 77.2719},
        "Punjabi Bagh, Delhi - DPCC": {"name": "Punjabi Bagh", "lat": 28.6667, "lon": 77.1333},
        "Wazirpur, Delhi - DPCC": {"name": "Wazirpur", "lat": 28.6981, "lon": 77.1649}
    }
}

async def test_fetch():
    print("Testing CPCB fetch for Delhi...")
    print(f"Station IDs: {delhi_config['station_ids']}")

    try:
        records = await fetch_city_aqi(delhi_config)
        print(f"\nReceived {len(records)} records")

        live_count = sum(1 for r in records if r.get("data_source") == "live")
        mock_count = sum(1 for r in records if r.get("data_source") == "mock")

        print(f"Live records: {live_count}")
        print(f"Mock records: {mock_count}")

        if live_count > 0:
            print("\nLIVE DATA RECEIVED:")
            for record in records:
                if record.get("data_source") == "live":
                    print(f"  Station: {record['station_id']}")
                    print(f"    AQI: {record['aqi']}")
                    print(f"    PM2.5: {record['pm25']}")
                    print(f"    PM10: {record['pm10']}")
                    print(f"    NO2: {record['no2']}")
                    print(f"    Timestamp: {record['timestamp']}")
                    print()
        else:
            print("\nNO LIVE DATA - ALL MOCK")
            if records:
                print("First mock record:")
                print(f"  {records[0]}")

    except Exception as e:
        print(f"ERROR: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    asyncio.run(test_fetch())