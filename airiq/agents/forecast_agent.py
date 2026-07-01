"""
agents/forecast_agent.py
------------------------
Agent 2 — AQI Forecast using XGBoost.

This agent generates multi-step predictions for hours [1, 3, 6, 12, 24, 48, 72]
ahead by recursively projecting AQI using an autoregressive XGBoost model.
If the XGBoost model is unavailable, it falls back to a diurnal-adjusted
persistence baseline.

Loaded models are resolved relative to the package directory.
"""

import os
import json
import logging
import math
import datetime
from pathlib import Path
import joblib

logger = logging.getLogger("airiq.forecast_agent")

# ── Dynamic Model Loading ──────────────────────────────────────────────────
ROOT_DIR = Path(__file__).resolve().parents[1]
MODELS_DIR = ROOT_DIR / "models"
MODEL_PATH = MODELS_DIR / "forecast_model.pkl"
FEATURES_PATH = MODELS_DIR / "feature_names.json"
METRICS_PATH = MODELS_DIR / "metrics.json"

_model = None
_feature_names = None
_metrics = {}

try:
    if MODEL_PATH.exists() and FEATURES_PATH.exists():
        _model = joblib.load(MODEL_PATH)
        with open(FEATURES_PATH, encoding="utf-8") as f:
            _feature_names = json.load(f)
        if METRICS_PATH.exists():
            with open(METRICS_PATH, encoding="utf-8") as f:
                _metrics = json.load(f)
        logger.info("XGBoost forecast model loaded successfully.")
    else:
        logger.warning("XGBoost model files not found. Using persistence fallback.")
except Exception as exc:
    logger.warning("Error loading XGBoost forecast model: %s. Using persistence fallback.", exc)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def get_aqi_category(aqi: int) -> str:
    """Return India CPCB AQI category name for the given index."""
    if aqi <= 50:
        return "Good"
    elif aqi <= 100:
        return "Satisfactory"
    elif aqi <= 200:
        return "Moderate"
    elif aqi <= 300:
        return "Poor"
    elif aqi <= 400:
        return "Very Poor"
    else:
        return "Severe"


def persistence_baseline(current_aqi: int, target_hour: int) -> int:
    """
    Computes a persistence forecast adjusted for the target hour's typical
    diurnal pattern in Indian cities.
    
    Morning (6–9am): ×1.15
    Midday (10am–4pm): ×0.90
    Evening rush (5–8pm): ×1.20
    Night (9pm–5am): ×0.85
    """
    if 6 <= target_hour <= 9:
        factor = 1.15
    elif 17 <= target_hour <= 20:
        factor = 1.20
    elif 10 <= target_hour <= 16:
        factor = 0.90
    else:
        factor = 0.85
        
    return int(round(current_aqi * factor))


# ---------------------------------------------------------------------------
# Main forecast runner
# ---------------------------------------------------------------------------

async def run_forecast(
    current_aqi: int,
    current_conditions: dict,      # {wind_speed, wind_direction, humidity, boundary_layer_height}
    weather_forecast_72h: list,    # list of hourly dicts from Open-Meteo
    city_config: dict,
    station_id: str = None
) -> dict:
    """
    Generate predictions for hours [1, 3, 6, 12, 24, 48, 72] ahead.

    If the XGBoost model is successfully loaded, predictions are made using
    a recursive multi-step forecasting process. Otherwise, it uses the
    persistence baseline with diurnal adjustments.
    """
    # ── Timezone parsing ─────────────────────────────────────────────────────
    tz_name = city_config.get("timezone", "Asia/Kolkata")
    try:
        from zoneinfo import ZoneInfo
        tz = ZoneInfo(tz_name)
    except Exception:
        # Fallback if zoneinfo is not available or database is missing
        from datetime import timezone as dt_timezone, timedelta
        tz = dt_timezone(timedelta(hours=5, minutes=30))
        
    now = datetime.datetime.now(tz)
    
    # ── Autoregressive history buffer ────────────────────────────────────────
    # Initialize the last 24 hours of AQI with the current AQI value
    aqi_buffer = [current_aqi] * 24
    
    # ── Weather values mapping ───────────────────────────────────────────────
    # Current values as fallback baseline
    ws = current_conditions.get("wind_speed", 5.0)
    wd = current_conditions.get("wind_direction", 180.0)
    hum = current_conditions.get("humidity", 60.0)
    blh = current_conditions.get("boundary_layer_height", 800.0)
    
    weather_by_offset = {}
    for i, w_entry in enumerate(weather_forecast_72h):
        weather_by_offset[i + 1] = {
            "wind_speed": w_entry.get("wind_speed", ws),
            "wind_direction": w_entry.get("wind_direction", wd),
            "humidity": w_entry.get("humidity", hum),
            "boundary_layer_height": w_entry.get("boundary_layer_height", blh)
        }
        
    forecast_results = []
    target_horizons = {1, 3, 6, 12, 24, 48, 72}
    model_used = "persistence_baseline"
    
    if _model is not None and _feature_names is not None:
        model_used = "xgboost"
        
    # ── Forecast projection loop ─────────────────────────────────────────────
    for h in range(1, 73):
        target_time = now + datetime.timedelta(hours=h)
        target_hour = target_time.hour
        
        # Weather values for this future hour
        w_step = weather_by_offset.get(h, {
            "wind_speed": ws,
            "wind_direction": wd,
            "humidity": hum,
            "boundary_layer_height": blh
        })
        
        if model_used == "xgboost":
            # Time features
            hour = target_hour
            day_of_week = target_time.weekday()
            month = target_time.month
            is_weekend = int(day_of_week >= 5)
            
            # Lag features from sliding buffer window
            lag_1h = aqi_buffer[-1]
            lag_6h = aqi_buffer[-6]
            lag_24h = aqi_buffer[-24]
            rolling_mean_3h = sum(aqi_buffer[-3:]) / 3.0
            
            # Encoded weather wind features
            w_speed = w_step["wind_speed"]
            w_dir = w_step["wind_direction"]
            w_dir_sin = math.sin(math.radians(w_dir))
            w_dir_cos = math.cos(math.radians(w_dir))
            w_hum = w_step["humidity"]
            w_blh = w_step["boundary_layer_height"]
            
            # Build feature dict matching exact trained columns
            feat_dict = {
                "hour": hour,
                "day_of_week": day_of_week,
                "month": month,
                "is_weekend": is_weekend,
                "lag_1h": lag_1h,
                "lag_6h": lag_6h,
                "lag_24h": lag_24h,
                "rolling_mean_3h": rolling_mean_3h,
                "wind_speed": w_speed,
                "wind_direction_sin": w_dir_sin,
                "wind_direction_cos": w_dir_cos,
                "humidity": w_hum,
                "boundary_layer_height": w_blh
            }
            
            # Build list in correct feature order
            feat_vector = [feat_dict[col] for col in _feature_names]
            
            try:
                # Predict 1 step ahead recursively
                pred = _model.predict([feat_vector])[0]
                predicted_aqi = max(0, int(round(pred)))
            except Exception as exc:
                logger.warning("XGBoost predict failed at hour %d: %s. Falling back to persistence.", h, exc)
                predicted_aqi = persistence_baseline(current_aqi, target_hour)
        else:
            predicted_aqi = persistence_baseline(current_aqi, target_hour)
            
        # Add forecast value to rolling history window
        aqi_buffer.append(predicted_aqi)
        
        # If this hour is in our target output set, save it
        if h in target_horizons:
            forecast_results.append({
                "hours_ahead": h,
                "predicted_aqi": predicted_aqi,
                "category": get_aqi_category(predicted_aqi),
                "method": "xgboost" if model_used == "xgboost" else "persistence_baseline"
            })
            
    # Find peak in the next 24 hours
    peak_aqi = -1
    peak_hour = -1
    for h, val in enumerate(aqi_buffer[24:48], start=1):
        if val > peak_aqi:
            peak_aqi = val
            peak_hour = (now + datetime.timedelta(hours=h)).hour
            
    rmse_vs_baseline = _metrics.get("rmse_xgb")
    
    return {
        "forecast": forecast_results,
        "peak_24h": {
            "aqi": peak_aqi,
            "hour": peak_hour,
            "category": get_aqi_category(peak_aqi)
        },
        "model_used": model_used,
        "rmse_vs_baseline": rmse_vs_baseline
    }
