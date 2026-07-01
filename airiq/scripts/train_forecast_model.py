"""
scripts/train_forecast_model.py
--------------------------------
One-time script to train the XGBoost AQI forecast model.
Loads historical Bengaluru data, engineers features, trains the model,
evaluates it against a persistence baseline, and saves the artifacts.

If the historical CSV is missing or empty, it automatically generates
realistic synthetic data so the pipeline works out-of-the-box.
"""

import os
import json
import logging
import numpy as np
import pandas as pd
import joblib
from pathlib import Path
from xgboost import XGBRegressor
from sklearn.metrics import mean_squared_error

# Setup logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
logger = logging.getLogger("train_forecast_model")

# Define paths
ROOT_DIR = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT_DIR / "scripts" / "data"
CSV_PATH = DATA_DIR / "bengaluru_historical.csv"
MODELS_DIR = ROOT_DIR / "models"
MODEL_PATH = MODELS_DIR / "forecast_model.pkl"
FEATURES_PATH = MODELS_DIR / "feature_names.json"
METRICS_PATH = MODELS_DIR / "metrics.json"

# Make sure output directories exist
DATA_DIR.mkdir(parents=True, exist_ok=True)
MODELS_DIR.mkdir(parents=True, exist_ok=True)

def generate_mock_historical_data():
    """Generates realistic synthetic 6-month historical data for Bengaluru."""
    logger.info("Generating realistic mock historical data for training...")
    end_time = pd.Timestamp.now(tz="UTC")
    start_time = end_time - pd.Timedelta(days=180)
    timestamps = pd.date_range(start=start_time, end=end_time, freq="h")
    
    stations = ["KARN001", "KARN002", "KARN003", "KARN004"]
    dfs = []
    
    for station in stations:
        # Base AQI for station
        np.random.seed(hash(station) % 2**32)
        base_aqi = 85 + np.random.randint(-15, 20)
        
        # Simulate diurnal cycle, weekly cycle, and random walk
        hours = timestamps.hour
        dayofweek = timestamps.dayofweek
        
        # Diurnal pattern: peaks around 8 AM and 7 PM
        diurnal = 15 * np.sin(2 * np.pi * (hours - 5) / 24) + 10 * np.sin(4 * np.pi * (hours - 15) / 24)
        # Weekly pattern: slightly higher on weekdays
        weekly = 8 * (dayofweek < 5).astype(int) - 5
        
        # Random walk for meteorological variation
        steps = np.random.normal(0, 3, size=len(timestamps))
        random_walk = np.cumsum(steps)
        # Detrend random walk using a rolling window or high pass filter to prevent runaway values
        random_walk = random_walk - pd.Series(random_walk).rolling(168, min_periods=1).mean().values
        
        aqi = base_aqi + diurnal + weekly + random_walk
        aqi = np.clip(aqi, 20, 450)  # sensible bounds
        
        # Synthesize pollutants based on AQI
        pm25 = aqi * 0.55 + np.random.normal(0, 5, size=len(aqi))
        pm10 = aqi * 0.90 + np.random.normal(0, 10, size=len(aqi))
        no2 = 25 + 0.15 * aqi + np.random.normal(0, 3, size=len(aqi))
        
        # Create station dataframe
        station_df = pd.DataFrame({
            "timestamp": timestamps,
            "station_id": station,
            "pm25": np.clip(pm25, 2, 350).round(1),
            "pm10": np.clip(pm10, 5, 500).round(1),
            "no2": np.clip(no2, 2, 150).round(1),
            "aqi": aqi.astype(int),
            # Add weather features directly
            "wind_speed": np.random.uniform(2, 25, size=len(aqi)).round(1),
            "wind_direction": np.random.uniform(0, 360, size=len(aqi)).round(1),
            "humidity": np.random.uniform(30, 95, size=len(aqi)).round(1),
            "boundary_layer_height": np.random.uniform(300, 2000, size=len(aqi)).round(1)
        })
        dfs.append(station_df)
        
    df = pd.concat(dfs, ignore_index=True)
    df.to_csv(CSV_PATH, index=False)
    logger.info("Mock historical data written to %s", CSV_PATH)

def load_data() -> pd.DataFrame:
    """Loads dataset. Generates mock if missing or empty."""
    if not CSV_PATH.exists() or CSV_PATH.stat().st_size == 0:
        generate_mock_historical_data()
        
    df = pd.read_csv(CSV_PATH)
    df["timestamp"] = pd.to_datetime(df["timestamp"])
    
    # Verify we have weather columns, if not generate them randomly
    weather_cols = ["wind_speed", "wind_direction", "humidity", "boundary_layer_height"]
    for col in weather_cols:
        if col not in df.columns:
            logger.info("Weather column '%s' missing from CSV, generating synthetic values", col)
            if col == "wind_speed":
                df[col] = np.random.uniform(3, 20, size=len(df))
            elif col == "wind_direction":
                df[col] = np.random.uniform(0, 360, size=len(df))
            elif col == "humidity":
                df[col] = np.random.uniform(40, 90, size=len(df))
            elif col == "boundary_layer_height":
                df[col] = np.random.uniform(400, 1800, size=len(df))
                
    return df

def build_features(df: pd.DataFrame) -> pd.DataFrame:
    """Engineers time, lag, rolling, and weather features per station."""
    logger.info("Engineering features...")
    processed_dfs = []
    
    # Process each station individually to avoid cross-station contamination of lags
    for station_id, group in df.groupby("station_id"):
        group = group.sort_values("timestamp").copy()
        
        # Time features
        group["hour"] = group["timestamp"].dt.hour
        group["day_of_week"] = group["timestamp"].dt.dayofweek
        group["month"] = group["timestamp"].dt.month
        group["is_weekend"] = (group["day_of_week"] >= 5).astype(int)
        
        # Lag features (relative to timestamp t)
        group["lag_1h"] = group["aqi"].shift(1)
        group["lag_6h"] = group["aqi"].shift(6)
        group["lag_24h"] = group["aqi"].shift(24)
        
        # Rolling features
        group["rolling_mean_3h"] = group["lag_1h"].rolling(3).mean()
        
        # Weather features
        group["wind_direction_sin"] = np.sin(np.radians(group["wind_direction"]))
        group["wind_direction_cos"] = np.cos(np.radians(group["wind_direction"]))
        
        # Target: aqi (next hour)
        group["target"] = group["aqi"].shift(-1)
        
        processed_dfs.append(group)
        
    df_feat = pd.concat(processed_dfs, ignore_index=True)
    
    # Drop rows with NaN targets or NaN lag features due to shift
    df_feat = df_feat.dropna(subset=["target", "lag_1h", "lag_6h", "lag_24h", "rolling_mean_3h"])
    return df_feat

def main():
    # Load and clean
    df = load_data()
    logger.info("Loaded dataset with %d rows", len(df))
    
    # Feature engineering
    df_feat = build_features(df)
    logger.info("Processed dataset has %d complete rows after feature engineering", len(df_feat))
    
    # Feature list
    feature_names = [
        "hour", "day_of_week", "month", "is_weekend",
        "lag_1h", "lag_6h", "lag_24h", "rolling_mean_3h",
        "wind_speed", "wind_direction_sin", "wind_direction_cos",
        "humidity", "boundary_layer_height"
    ]
    
    # Ensure all feature columns are numeric
    for col in feature_names:
        df_feat[col] = pd.to_numeric(df_feat[col])
        
    # Split train/test (last 30 days as test, rest as train)
    max_date = df_feat["timestamp"].max()
    split_date = max_date - pd.Timedelta(days=30)
    
    train_df = df_feat[df_feat["timestamp"] < split_date]
    test_df = df_feat[df_feat["timestamp"] >= split_date]
    
    logger.info("Train set: %d rows (before %s)", len(train_df), split_date)
    logger.info("Test set: %d rows (after %s)", len(test_df), split_date)
    
    if len(train_df) == 0 or len(test_df) == 0:
        logger.error("Not enough data to split into train and test sets. Adjusting split to 80/20.")
        df_feat = df_feat.sort_values("timestamp")
        split_idx = int(len(df_feat) * 0.8)
        train_df = df_feat.iloc[:split_idx]
        test_df = df_feat.iloc[split_idx:]
        
    X_train = train_df[feature_names]
    y_train = train_df["target"]
    X_test = test_df[feature_names]
    y_test = test_df["target"]
    
    # ── Train XGBoost model ──────────────────────────────────────────────────
    logger.info("Training XGBoost Regressor...")
    model = XGBRegressor(
        n_estimators=200,
        max_depth=6,
        learning_rate=0.05,
        random_state=42,
        n_jobs=-1
    )
    model.fit(X_train, y_train)
    
    # ── Evaluate model vs Persistence baseline ────────────────────────────────
    # Persistence baseline predicts next hour AQI = current AQI (which is lag_1h relative to next hour)
    # Wait, the target is AQI at t+1. The current AQI at time t is `df_feat['aqi']` or the feature `lag_1h` if we match index,
    # but let's look at the mapping:
    # target = df['aqi'].shift(-1) (which is AQI at t+1)
    # The current AQI at time t is `df_feat['aqi']`.
    # So the persistence prediction for target (t+1) is the AQI at time t.
    y_pred = model.predict(X_test)
    y_persistence = test_df["aqi"] # AQI at time t
    
    rmse_xgb = np.sqrt(mean_squared_error(y_test, y_pred))
    rmse_pers = np.sqrt(mean_squared_error(y_test, y_persistence))
    
    improvement = ((rmse_pers - rmse_xgb) / rmse_pers) * 100
    
    print(f"\n{'='*55}")
    print(f"XGBoost RMSE: {rmse_xgb:.2f} | Persistence RMSE: {rmse_pers:.2f} | Improvement: {improvement:.2f}%")
    print(f"{'='*55}\n")
    
    # Save model and feature names
    joblib.dump(model, MODEL_PATH)
    with open(FEATURES_PATH, "w") as f:
        json.dump(feature_names, f)
        
    # Save metrics for forecast agent loading
    metrics = {
        "rmse_xgb": rmse_xgb,
        "rmse_persistence": rmse_pers,
        "improvement_pct": improvement
    }
    with open(METRICS_PATH, "w") as f:
        json.dump(metrics, f)
        
    logger.info("Successfully saved model to %s", MODEL_PATH)
    logger.info("Successfully saved features list to %s", FEATURES_PATH)

if __name__ == "__main__":
    main()
