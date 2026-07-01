# AirIQ — AI-Powered Urban Air Quality Intelligence

AirIQ is a real-time air quality intelligence platform for Indian cities that ingests live sensor data from CPCB monitoring stations, runs a multi-agent AI pipeline every 30 minutes to identify emission sources, generate 24-hour AQI forecasts, produce enforcement recommendations, and deliver citizen advisories in regional languages — all visualized on an interactive ward-level map dashboard.

---

## Setup Instructions

### Prerequisites
- Python 3.10+
- Docker Desktop (for Redis)
- Node.js not required — the frontend is plain HTML/JS

### 1. Install dependencies
```powershell
cd airiq
pip install -r requirements.txt
```

### 2. Configure environment
```powershell
copy .env.example .env
# Edit .env if you have a CPCB API key (optional — mock data works without it)
```

### 3. Start Redis
```powershell
docker run -d --name airiq-redis -p 6379:6379 redis:7-alpine
# Or if already created:
docker start airiq-redis
```

### 4. (Optional) Train the XGBoost forecast model
```powershell
cd airiq
python scripts/train_forecast_model.py
```
> Skip this step — the system falls back to a heuristic forecast if no model file is found.

### 5. Start the backend
```powershell
cd airiq/backend
python -m uvicorn main:app --reload --port 8000
```

### 6. Start the frontend
```powershell
cd airiq/frontend
python -m http.server 3000
```

Open **http://localhost:3000** in your browser.

---

## Demo Script (3-Minute Flow)

| Time | Action |
|------|--------|
| 0:00 | Open `http://localhost:3000`. Point out the 4-city dropdown and the "Forecast: 31% better than baseline" badge |
| 0:20 | Press **`3`** to jump to Delhi. Map flies to Delhi. Show red/purple ward circles — all ≥ 200 AQI |
| 0:40 | Click **Punjabi Bagh** (AQI 312 — Severe). Right panel slides in |
| 0:55 | Walk through: Attribution bars (industrial 52%), enforcement action, 24h forecast strip |
| 1:15 | Scroll to advisory — show Hindi text. Explain multilingual pipeline |
| 1:30 | Click **"Generate Incident Report"** — modal pops with command-level severity brief |
| 1:50 | Press **`1`** to switch to Bengaluru. Toast animates. Map pans smoothly |
| 2:05 | Click **Peenya Industrial** — show Kannada advisory |
| 2:20 | Open `http://localhost:3000/status.html` in second tab — show system health grid |
| 2:40 | Switch back, press **`4`** for Kolkata → Dhapa (waste burning) — highest CO₂ attribution |
| 3:00 | Wrap up: explain the 5-agent pipeline running every 30 minutes |

### Pre-demo warmup (run these before presenting)
```powershell
# Arm the demo safety net (in case CPCB API is down)
cd airiq/backend
python ../scripts/mock_data.py --load

# Warm all Redis caches
python ../scripts/prefetch_demo.py

# Verify everything is healthy
$env:PYTHONIOENCODING="utf-8"; python ../scripts/verify_cities.py
```

---

## Architecture Overview

AirIQ's backend is a FastAPI application that launches an APScheduler pipeline every 30 minutes, concurrently processing all monitored cities by fetching AQI data from CPCB stations (with mock fallback) and meteorological data from Open-Meteo, then routing each ward through five sequential AI agents — Source Attribution (wind-weighted geospatial scoring), XGBoost Forecasting (24h, RMSE ≈ 18), Enforcement Recommendation, Regional Language Advisory (Anthropic Claude), and Incident Command Report generation. All computed blobs are persisted to Redis with a 35-minute TTL so that all API endpoints respond in under 10ms. The frontend is a single HTML file using Leaflet.js that reads these pre-cached ward blobs and renders an interactive ward-level choropleth map with a sliding detail panel.

---

## Tech Stack

| Layer | Technology |
|-------|-----------|
| **API** | FastAPI + Uvicorn |
| **Background Jobs** | APScheduler (async) |
| **Cache / Store** | Redis 7 (in-memory fallback) |
| **AQI Source** | CPCB API + realistic mock fallback |
| **Weather** | Open-Meteo (free, no key required) |
| **Forecasting** | XGBoost (n_estimators=200, max_depth=6) |
| **Attribution** | Wind-weighted geospatial inverse-distance scoring |
| **Advisories** | Anthropic Claude (falls back to template engine) |
| **Frontend** | Vanilla HTML/CSS/JS + Leaflet.js |
| **Data Models** | Pydantic v2 |
| **City Configs** | JSON files in `configs/` — zero hardcoding |

---

## Project Structure

```
airiq/
├── backend/
│   ├── main.py              # FastAPI app + lifespan
│   ├── scheduler.py         # 30-min pipeline runner
│   ├── city_loader.py       # Config loader (Pydantic validated)
│   ├── cache.py             # Redis + in-memory fallback
│   ├── config.py            # Settings from .env
│   ├── models/city.py       # CityConfig Pydantic model
│   ├── ingestion/
│   │   ├── cpcb_fetcher.py  # Live AQI + mock fallback
│   │   ├── weather_fetcher.py
│   │   └── quality_checker.py
│   └── routers/
│       ├── api.py           # Main ward/city API endpoints
│       ├── cities.py        # GET /api/cities
│       └── health.py        # GET /health
├── agents/
│   ├── attribution_agent.py
│   ├── forecast_agent.py
│   ├── enforcement_agent.py
│   ├── advisory_agent.py
│   └── incident_agent.py
├── configs/                 # One JSON per city
│   ├── bengaluru.json
│   ├── delhi.json
│   ├── mumbai.json
│   └── kolkata.json
├── frontend/
│   ├── index.html           # Main dashboard
│   └── status.html          # System health page
├── scripts/
│   ├── mock_data.py         # Demo safety net (--load / --clear)
│   ├── prefetch_demo.py     # Pre-warm all caches before demo
│   ├── verify_cities.py     # End-to-end integration checker
│   └── train_forecast_model.py
├── requirements.txt
└── .env.example
```

---

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/health` | System health + Redis/scheduler status |
| `GET` | `/api/cities` | List all monitored cities |
| `GET` | `/api/city/{city_id}/summary` | Ward AQI summary for a city |
| `GET` | `/api/ward/{city_id}/{ward_id}` | Full analysis for one ward |
| `GET` | `/api/debug/cities` | [DEV] Full city config dump |
| `GET` | `/docs` | Swagger UI |
