"""
scripts/fetch_historical.py
----------------------------
One-time script: downloads or processes 6 months of historical AQI data for
Bengaluru and saves a clean CSV to scripts/data/bengaluru_historical.csv.

Usage:
  # Pull from data.gov.in API (requires CPCB_API_KEY in environment / .env)
  python fetch_historical.py

  # Use an already-downloaded local CSV instead
  python fetch_historical.py --csv /path/to/raw_data.csv

Output columns: timestamp, station_id, pm25, pm10, no2, aqi
"""

import argparse
import os
import sys
import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path
import logging

# ── make sure the backend package is importable when run from scripts/ ──────
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

import httpx
import pandas as pd
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[1] / ".env")

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
logger = logging.getLogger("fetch_historical")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
CPCB_BASE_URL   = "https://api.data.gov.in/resource/3b01bcb8-0b14-4abf-b6f2-c1bfd384ba69"
CITY_NAME       = "Bengaluru"
OUTPUT_DIR      = Path(__file__).parent / "data"
OUTPUT_PATH     = OUTPUT_DIR / "bengaluru_historical.csv"
MONTHS_BACK     = 6
PAGE_LIMIT      = 500          # records per API page


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _safe_float(val) -> float | None:
    """Convert a value to float, returning None on failure."""
    try:
        return float(val)
    except (TypeError, ValueError):
        return None


def _clean_dataframe(df: pd.DataFrame) -> pd.DataFrame:
    """Normalise raw API / CSV data into the canonical schema."""
    # ── Flexible column mapping (API field names vary between CPCB versions) ──
    col_map = {
        # timestamp aliases
        "from_date":      "timestamp",
        "last_update":    "timestamp",
        "sampling_date":  "timestamp",
        "date":           "timestamp",
        # station aliases
        "station_id":     "station_id",
        "station_code":   "station_id",
        "id":             "station_id",
        # pollutant aliases
        "pm2.5":          "pm25",
        "pm_2_5":         "pm25",
        "pm25":           "pm25",
        "pm10":           "pm10",
        "no2":            "no2",
        "aqi":            "aqi",
        "air_quality_index": "aqi",
    }

    # Lowercase column names for safe mapping
    df.columns = [c.lower().strip() for c in df.columns]
    df = df.rename(columns={k: v for k, v in col_map.items() if k in df.columns})

    # Ensure all required columns exist (fill missing with None)
    for col in ["timestamp", "station_id", "pm25", "pm10", "no2", "aqi"]:
        if col not in df.columns:
            df[col] = None

    # ── Type coercions ──────────────────────────────────────────────────────
    df["timestamp"] = pd.to_datetime(df["timestamp"], errors="coerce", utc=True)
    for col in ["pm25", "pm10", "no2", "aqi"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")

    # ── Filter to target city rows if a 'city'/'station' column is present ──
    for city_col in ["city", "city_name", "location"]:
        if city_col in df.columns:
            mask = df[city_col].str.contains(CITY_NAME, case=False, na=False)
            if mask.any():
                df = df[mask]
            break

    # ── Drop rows with no timestamp or station ──────────────────────────────
    df = df.dropna(subset=["timestamp", "station_id"])
    df["station_id"] = df["station_id"].astype(str).str.strip()

    # ── Keep only target columns and sort ───────────────────────────────────
    df = df[["timestamp", "station_id", "pm25", "pm10", "no2", "aqi"]].copy()
    df = df.sort_values("timestamp").reset_index(drop=True)
    return df


# ---------------------------------------------------------------------------
# API download path
# ---------------------------------------------------------------------------
async def _fetch_from_api(api_key: str) -> pd.DataFrame:
    """Pages through the data.gov.in CPCB API for the last MONTHS_BACK months."""
    since = datetime.now(timezone.utc) - timedelta(days=30 * MONTHS_BACK)
    since_str = since.strftime("%d/%m/%Y")          # CPCB format

    all_records: list[dict] = []
    offset = 0

    async with httpx.AsyncClient(timeout=30) as client:
        while True:
            params = {
                "api-key": api_key,
                "format": "json",
                "limit":  PAGE_LIMIT,
                "offset": offset,
                "filters[city]":      CITY_NAME,
                "filters[from_date]": since_str,
            }
            logger.info("Fetching page offset=%d …", offset)
            try:
                resp = await client.get(CPCB_BASE_URL, params=params)
                resp.raise_for_status()
                payload = resp.json()
            except Exception as exc:
                logger.error("API error at offset %d: %s", offset, exc)
                break

            records = payload.get("records", [])
            if not records:
                logger.info("No more records — stopping at offset %d.", offset)
                break

            all_records.extend(records)
            logger.info("  fetched %d records (total so far: %d)", len(records), len(all_records))

            # If the page was smaller than the limit we've hit the end
            if len(records) < PAGE_LIMIT:
                break
            offset += PAGE_LIMIT

    if not all_records:
        logger.warning("API returned zero records for %s. Check your API key and city filter.", CITY_NAME)
        return pd.DataFrame()

    return pd.DataFrame(all_records)


# ---------------------------------------------------------------------------
# Local CSV path
# ---------------------------------------------------------------------------
def _load_local_csv(path: str) -> pd.DataFrame:
    logger.info("Loading local CSV: %s", path)
    return pd.read_csv(path, low_memory=False)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
async def main():
    parser = argparse.ArgumentParser(description="Fetch/process historical AQI for Bengaluru")
    parser.add_argument("--csv", type=str, default=None,
                        help="Path to a local raw CSV file. If omitted, downloads from API.")
    args = parser.parse_args()

    # ── Source selection ────────────────────────────────────────────────────
    if args.csv:
        raw_df = _load_local_csv(args.csv)
    else:
        api_key = os.getenv("CPCB_API_KEY", "").strip()
        if not api_key:
            logger.error(
                "CPCB_API_KEY is not set. Either set it in .env or pass --csv <file>."
            )
            sys.exit(1)
        raw_df = await _fetch_from_api(api_key)

    if raw_df.empty:
        logger.error("No data obtained. Exiting.")
        sys.exit(1)

    # ── Clean & save ────────────────────────────────────────────────────────
    logger.info("Cleaning %d raw rows…", len(raw_df))
    clean_df = _clean_dataframe(raw_df)

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    clean_df.to_csv(OUTPUT_PATH, index=False)

    print(f"\n{'='*55}")
    print(f"  Saved:  {OUTPUT_PATH}")
    print(f"  Rows:   {len(clean_df):,}")
    print(f"  Cols:   {list(clean_df.columns)}")
    if not clean_df.empty and "timestamp" in clean_df.columns:
        print(f"  Range:  {clean_df['timestamp'].min()} → {clean_df['timestamp'].max()}")
    print(f"{'='*55}\n")


if __name__ == "__main__":
    asyncio.run(main())
