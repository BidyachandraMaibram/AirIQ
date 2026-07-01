"""
backend/ingestion/quality_checker.py
--------------------------------------
Evaluates data quality of each AQI record and attaches three fields:

  quality_score      float 0.0–1.0   (1.0 = perfect, −0.25 per flag)
  confidence_modifier float 0.5–1.0  (agents multiply their confidence by this)
  quality_flags       list[str]      (e.g. ["stale", "anomalous_high"])

Flag rules
----------
  "missing"        — aqi is None or absent
  "anomalous_high" — aqi > 500
  "anomalous_low"  — aqi < 0
  "stale"          — timestamp more than 60 minutes old
  "offline"        — timestamp more than 2 hours old
"""

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

logger = logging.getLogger("airiq.quality_checker")

# ---------------------------------------------------------------------------
# Thresholds
# ---------------------------------------------------------------------------
AQI_MAX          = 500
AQI_MIN          = 0
STALE_MINUTES    = 60     # flag as "stale" after this many minutes
OFFLINE_MINUTES  = 120    # flag as "offline" after this many minutes
SCORE_PENALTY    = 0.25   # deducted per flag
MIN_SCORE        = 0.0
MIN_CONFIDENCE   = 0.5    # confidence floor (agents still trust the data a little)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _parse_timestamp(ts: Any) -> datetime | None:
    """Try to parse a timestamp string/datetime to a UTC-aware datetime."""
    if ts is None:
        return None
    if isinstance(ts, datetime):
        return ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)
    if isinstance(ts, str):
        for fmt in (
            "%Y-%m-%dT%H:%M:%S%z",
            "%Y-%m-%dT%H:%M:%SZ",
            "%Y-%m-%dT%H:%M:%S",
            "%d/%m/%Y %H:%M",
            "%Y-%m-%d %H:%M:%S",
        ):
            try:
                dt = datetime.strptime(ts.strip(), fmt)
                return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
            except ValueError:
                continue
    return None


# ---------------------------------------------------------------------------
# Core quality checker
# ---------------------------------------------------------------------------
def check_quality(record: dict) -> dict:
    """
    Evaluate data quality and augment the record in-place.

    Parameters
    ----------
    record : dict
        A single AQI record (from cpcb_fetcher or any other source).
        Expected keys: aqi, timestamp, station_id.

    Returns
    -------
    The same dict with quality_score, confidence_modifier, quality_flags added.
    """
    record = dict(record)   # shallow copy — don't mutate caller's data
    flags: list[str] = []

    aqi = record.get("aqi")
    ts  = _parse_timestamp(record.get("timestamp"))
    now = datetime.now(timezone.utc)

    # ── Flag: missing ────────────────────────────────────────────────────────
    if aqi is None:
        flags.append("missing")

    else:
        # ── Flag: anomalous_high / anomalous_low ─────────────────────────────
        try:
            aqi_val = float(aqi)
            if aqi_val > AQI_MAX:
                flags.append("anomalous_high")
                logger.debug("Station %s: anomalous_high AQI %.1f", record.get("station_id"), aqi_val)
            elif aqi_val < AQI_MIN:
                flags.append("anomalous_low")
                logger.debug("Station %s: anomalous_low AQI %.1f", record.get("station_id"), aqi_val)
        except (TypeError, ValueError):
            flags.append("missing")   # aqi is present but not numeric

    # ── Flag: stale / offline (based on timestamp age) ───────────────────────
    if ts is None:
        # No timestamp at all — treat as missing/stale
        flags.append("stale")
    else:
        age_minutes = (now - ts).total_seconds() / 60
        if age_minutes > OFFLINE_MINUTES:
            flags.append("offline")   # offline implies stale — don't double-flag
        elif age_minutes > STALE_MINUTES:
            flags.append("stale")

    # ── Score calculation ─────────────────────────────────────────────────────
    penalty        = len(flags) * SCORE_PENALTY
    quality_score  = max(MIN_SCORE, 1.0 - penalty)
    confidence_mod = max(MIN_CONFIDENCE, quality_score)

    record["quality_flags"]       = flags
    record["quality_score"]       = round(quality_score, 4)
    record["confidence_modifier"] = round(confidence_mod, 4)

    if flags:
        logger.debug(
            "Station %s | flags=%s | quality=%.2f | confidence=%.2f",
            record.get("station_id"), flags, quality_score, confidence_mod,
        )

    return record


# ---------------------------------------------------------------------------
# Batch helper
# ---------------------------------------------------------------------------
def check_quality_batch(records: list[dict]) -> list[dict]:
    """Apply check_quality to a list of records and return the annotated list."""
    return [check_quality(r) for r in records]
