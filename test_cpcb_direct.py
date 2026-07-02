#!/usr/bin/env python3
"""Direct test of the CPCB fetcher to see if it gets live data."""

import asyncio
import sys
from pathlib import Path

# Add backend to path
backend_dir = Path(__file__).parent / "airiq" / "backend"
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

from ingestion.cpcb_fetcher import fetch_city_aqi
from config import settings

# Test with Delhi config
delhi_config = {
    "city_id": "delhi",
    "name": "Delhi",
    "lat": 28.6139,
    "lon": 77.2090,
    "station_ids": ["Anand Vihar", "Delhi College of Engineering", "Delhi Technological University (East Campus)", "Dhirpur", "Dwarka Sector 8", "IGI Airport", "Indira", "ITO", "L Vihar", "Mandard", "M Manda Nagla", "P", "Pit", "ISBT Maharana Pratap", "ITO", "Jamia Millia Islamia", "JLN Stadium", "JSVP Dwarka", "Lady Shri Ram College For Women", "Lodi Road", "Mandir Marg", "Marghazar Zoo", "Matiala", "Mayapuri", "Mundka", "Murthal", "Mundka Village", "Najafgarh", "Najafgarh Stadium", "Najafgarh Lake", "Nehru Nagar", "Nehru Place", "NH-8", "Nizamuddin", "NSIT Dwarka", "Pitampura", "Pusa", "Pusa (IITD)", "Pusa (Forest)", "Pusa (RIEA)", "Pusa (TDTEC)", "Punjabi Bagh", "R K Puram", "R K Puram (Sector 8)", "R K Puram (Sector 9)", "Rajokri", "Rajouri Garden", "Rithala", "Rohini", "Rohini Sector 9", "Rohini Sector 15", "Rohini Sector 16", "Rohini Sector 17", "Rohini Sector 18", "Rohini Sector 22", "Rohini Sector 23", "Rohini Sector 24", "Rohini Sector 25", "Rohini Sector 26", "Rohini Sector 27", "Rohini Sector 28", "Rohini Sector 31", "Rohini Sector 32", "Rohini Sector 33", "Rohini Sector 34", "Rohini Sector 35", "Rohini Sector 36", "Rohini Sector 37", "Rohini Sector 38", "Rohini Sector 39", "Rohini Sector 40", "Rohini Sector 41", "Rohini Sector 42", "Rohini Sector 43", "Rohini Sector 44", "Rohini Sector 45", "Rohini Sector 46", "Rohini Sector 47", "Rohini Sector 48", "Rohini Sector 49", "Rohini Sector 50", "Rohini Sector 51", "Rohini Sector 52", "Rohini Sector 53", "Rohini Sector 54", "Rohini Sector 55", "Rohini Sector 56", "Rohini Sector 57", "Rohini Sector 58", "Rohini Sector 59", "Rohini Sector 60", "Rohini Sector 61", "Rohini Sector 62", "Rohini Sector 63", "Rohini Sector 64", "Rohini Sector 65", "Rohini Sector 66", "Rohini Sector 67", "Rohini Sector 68", "Rohini Sector 69", "Rohini Sector 70", "Rohini Sector 71", "Rohini Sector 72", "Rohini Sector 73", "Rohini Sector 74", "Rohini Sector 75", "Rohini Sector 76", "Rohini Sector 77", "Rohini Sector 78", "Rohini Sector 79", "Rohini Sector 80", "Rohini Sector 81", "Rohini Sector 82", "Rohini Sector 83", "Rohini Sector 84", "Rohini Sector 85", "Rohini Sector 86", "Rohini Sector 87", "Rohini Sector 88", "Rohini Sector 89", "Rohini Sector 90", "Rohini Sector 91", "Rohini Sector 92", "Rohini Sector 93", "Rohini Sector 94", "Rohini Sector 95", "Rohini Sector 96", "Rohini Sector 97", "Rohini Sector 98", "Rohini Sector 99", "Rohini Sector 100"],
    "stations": {}
}

# Limit to a few stations for faster testing
delhi_config["station_ids"] = ["Anand Vihar", "Pusa", "R K Puram", "ISBT Maharana Pratap", "ITO"]

async def test_cpcb_fetch():
    print(f"Testing CPCB API with key: {settings.cpcb_api_key[:10]}...")
    print(f"Using config: {delhi_config['city_id']} - {delhi_config['name']}")

    try:
        records = await fetch_city_aqi(delhi_config)
        print(f"Got {len(records)} records")

        live_count = sum(1 for r in records if r.get("data_source") == "live")
        mock_count = sum(1 for r in records if r.get("data_source") == "mock")

        print(f"Live data: {live_count} records")
        print(f"Mock data: {mock_count} records")

        if live_count > 0:
            print("\nSUCCESS: Got live CPCB data!")
            # Show first live record
            for record in records:
                if record.get("data_source") == "live":
                    print(f"Sample live record: {record}")
                    break
        else:
            print("\nRESULT: Falling back to mock data (no live data retrieved)")
            if records:
                print(f"Sample mock record: {records[0]}")

    except Exception as e:
        print(f"ERROR: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    asyncio.run(test_cpcb_fetch())