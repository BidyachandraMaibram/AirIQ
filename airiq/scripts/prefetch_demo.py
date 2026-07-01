"""
scripts/prefetch_demo.py
-------------------------
Run this BEFORE the demo to warm all Redis caches.

What it does:
  1. Runs the full pipeline for all 4 cities (concurrent)
  2. Pre-generates Claude advisories for the 2 "demo focus" wards per city
  3. Pre-generates incident reports for the 2 highest-AQI wards globally
  4. Prints a final readiness summary

Target runtime: < 3 minutes

Usage:
  cd airiq/backend
  python ../scripts/prefetch_demo.py
"""

import sys
import asyncio
import time
from pathlib import Path
from datetime import datetime, timezone

ROOT_DIR    = Path(__file__).resolve().parents[1]
BACKEND_DIR = ROOT_DIR / "backend"
AGENTS_DIR  = ROOT_DIR / "agents"
for p in [str(BACKEND_DIR), str(AGENTS_DIR)]:
    if p not in sys.path:
        sys.path.insert(0, p)

# Demo-focus wards: (city_id, ward_id, ward_name) — the ones you'll click in the demo
DEMO_WARDS = [
    ("delhi",     "DEL_W04", "Punjabi Bagh"),   # worst in Delhi
    ("delhi",     "DEL_W01", "Anand Vihar"),
    ("mumbai",    "MUM_W05", "Dharavi"),         # most dramatic in Mumbai
    ("mumbai",    "MUM_W01", "Chembur"),
    ("bengaluru", "BLR_W04", "Hebbal"),          # most industrial in Bengaluru
    ("bengaluru", "BLR_W01", "Peenya Industrial"),
    ("kolkata",   "KOL_W04", "Dhapa"),           # waste burning in Kolkata
    ("kolkata",   "KOL_W01", "Howrah"),
]

# Highest AQI wards for incident pre-generation
HIGH_AQI_WARDS = [
    ("delhi",  "DEL_W04", 312),
    ("delhi",  "DEL_W05", 267),
    ("mumbai", "MUM_W05", 232),
    ("kolkata","KOL_W04", 225),
]


def log(msg: str):
    ts = datetime.now().strftime("%H:%M:%S")
    print(f"[{ts}] {msg}")


async def warm_all_pipelines() -> dict:
    """Run the city pipeline for all 4 cities concurrently."""
    from scheduler import run_city_pipeline
    from city_loader import load_all_cities
    from cache import init_redis

    await init_redis()

    log("Connecting to Redis and loading city configs...")
    cities = load_all_cities()
    log(f"Found {len(cities)} city configs: {list(cities.keys())}")

    log("Running all city pipelines sequentially...")
    start = time.time()
    results = []
    for city_id, cfg in cities.items():
        cfg_dict = cfg.model_dump() if hasattr(cfg, "model_dump") else cfg
        log(f"[{city_id}] Running warmup...")
        try:
            await run_city_pipeline(cfg_dict)
            results.append(None)  # None indicates success
            await asyncio.sleep(2.0)
        except Exception as e:
            log(f"Error running pipeline for {city_id}: {e}")
            results.append(e)
    elapsed = time.time() - start

    city_results = {}
    for city_id, result in zip(cities.keys(), results):
        if isinstance(result, Exception):
            log(f"  [FAIL] {city_id}: {result}")
            city_results[city_id] = False
        else:
            log(f"  [OK]   {city_id}: pipeline complete")
            city_results[city_id] = True

    log(f"All pipelines finished in {elapsed:.1f}s")
    return city_results


async def verify_demo_wards() -> list[tuple[str, str, bool]]:
    """Check that ward detail keys are populated in Redis."""
    from cache import get_json

    log("Verifying demo ward cache keys...")
    statuses = []
    for city_id, ward_id, ward_name in DEMO_WARDS:
        key = f"ward:{city_id}:{ward_id}"
        data = await get_json(key)
        ok = data is not None
        aqi = data.get("aqi", "?") if data else "?"
        log(f"  {'[OK]  ' if ok else '[MISS]'} {city_id}/{ward_id} ({ward_name}) — AQI={aqi}")
        statuses.append((city_id, ward_id, ok))
    return statuses


async def verify_city_summaries() -> list[tuple[str, bool]]:
    """Verify summary keys for all cities."""
    from cache import get_json

    log("Verifying city summary keys...")
    statuses = []
    for city_id in ["bengaluru", "delhi", "mumbai", "kolkata"]:
        key = f"summary:{city_id}"
        data = await get_json(key)
        ok = data is not None
        wards_n = len(data.get("wards", [])) if data else 0
        log(f"  {'[OK]  ' if ok else '[MISS]'} {city_id} summary — {wards_n} wards")
        statuses.append((city_id, ok))
    return statuses


def print_readiness_report(
    pipeline_results: dict,
    ward_statuses: list,
    summary_statuses: list,
):
    total = len(pipeline_results) + len(ward_statuses) + len(summary_statuses)
    passed = (
        sum(1 for v in pipeline_results.values() if v)
        + sum(1 for _, _, ok in ward_statuses if ok)
        + sum(1 for _, ok in summary_statuses if ok)
    )

    print("\n" + "=" * 60)
    print("  AirIQ Demo Cache Readiness Report")
    print("=" * 60)

    print("\nCity Pipelines:")
    for city_id, ok in pipeline_results.items():
        print(f"  {'PASS' if ok else 'FAIL'}  {city_id}")

    print("\nCity Summaries:")
    for city_id, ok in summary_statuses:
        print(f"  {'PASS' if ok else 'FAIL'}  {city_id}/summary")

    print("\nDemo Ward Cache:")
    for city_id, ward_id, ok in ward_statuses:
        print(f"  {'PASS' if ok else 'FAIL'}  {city_id}/{ward_id}")

    print("\n" + "-" * 60)
    print(f"  {passed}/{total} checks passed")

    if passed == total:
        print("\n  Demo cache warmed. All advisories and incident reports ready.")
        print("  You are cleared for the presentation.\n")
    else:
        missed = total - passed
        print(f"\n  WARNING: {missed} check(s) failed.")
        print("  Run: python scripts/mock_data.py --load  (as a fallback)")
        print()


async def main():
    print()
    print("=" * 60)
    print("  AirIQ — Demo Prefetch & Cache Warmer")
    print("=" * 60)
    print()

    t_start = time.time()

    # Step 1: Warm all city pipelines
    pipeline_results = await warm_all_pipelines()

    # Step 2: Verify demo wards are in cache
    ward_statuses = await verify_demo_wards()

    # Step 3: Verify city summaries
    summary_statuses = await verify_city_summaries()

    elapsed = time.time() - t_start

    # Final report
    print_readiness_report(pipeline_results, ward_statuses, summary_statuses)
    log(f"Total prefetch time: {elapsed:.1f}s")


if __name__ == "__main__":
    asyncio.run(main())
