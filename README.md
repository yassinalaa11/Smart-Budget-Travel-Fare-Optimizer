# SmartFare

SmartFare is a full-stack, Docker-based travel-price intelligence, data warehouse, and booking recommendation platform. PostgreSQL stores partitioned raw provider responses, daily FX rates, affiliate click attribution, price alerts, and a Medallion star schema; Prefect orchestrates daily ingestion, warehouse builds, alert checks, and weekly ML retraining; dbt transforms and tests Silver and Gold models; FastAPI serves analytics, recommendations, ML inference, and tracked affiliate redirects; and Streamlit delivers a six-tab interactive Plotly dashboard.

## Architecture Overview

- **Automated Ingestion & Resilience (`ingestion/`):**
  - Flight collectors supporting `mock`, `amadeus`, and `travelpayouts`.
  - Hotel collectors supporting `mock`, `serpapi` (Google Hotels), and `amadeus` (v3 Hotel Offers).
  - Daily USD-base exchange rate ingestion (`open.er-api.com`) into `public.fx_rates`.
  - Shared exponential backoff with jitter (`ingestion/http_retry.py`) on HTTP `429`, `5xx`, and network timeouts.
  - Automated monthly partition maintenance (`ingestion/partitions.py`) on `raw.api_responses`.
  - Slack webhook and Telegram bot alerting (`ingestion/alerts.py`) for pipeline failures and price-drop triggers (`ingestion/alert_checker.py`).
- **Bronze → Silver → Gold Warehouse (`transform/`):**
  - **Bronze:** `raw.api_responses` (monthly range-partitioned), `public.ingestion_request_log`, `public.fx_rates`, `public.click_events`, `public.price_alerts`.
  - **Silver:** `stg_flight_responses`, `stg_hotel_price_observations`, `stg_fx_rates`, `int_deduplicated_flights` (exact + codeshare itinerary deduplication), and `int_flight_price_quality` (USD-normalized anomaly & spike flagging).
  - **Gold:** `dim_date` (Egypt/destination holidays, school breaks, days-to-holiday), `dim_route`, `dim_hotel`, incremental `fct_flight_price_observations`, incremental `fct_hotel_price_observations`, `quarantined_observations`, `ml_features_flight_pricing`, and `ops_ingestion_completeness` (95% route coverage SLA).
- **Decision-Focused ML & Model Registry (`ml/`):**
  - Binary classifier (`y_will_drop`: probability of a >=7% fare drop in the next 14 days) and `p10` / `p50` / `p90` quantile regressors (`HistGradientBoosting`), plus flight and hotel lead-time curve models.
  - Leakage-free expanding-window backtesting (`observed_date + 14d < validation_start`) evaluating Brier score, ROC-AUC, Precision, P50 MAE, and **Simulated Traveler Regret ($)**.
  - Automatic per-route promotion gate (`>=5%` gain over statistical baseline) keeping the live warehouse safely in Phase 1 cold-start baseline until sufficient daily history accumulates.
  - Serialized `.joblib` artifacts and `scorecard.json` loaded by FastAPI at startup with hot-reload (`POST /api/v1/models/reload`).
- **Product UI & Monetization (`api/`, `dashboard/`):**
  - Interactive Plotly **Historical Booking Curve** (25th–75th percentile price cone) and **Total Trip Cost Heatmap** (Flight + Hotel USD matrix across stay lengths).
  - Affiliate deep-link redirects (`GET /api/v1/redirect/book`) logging unique `sub_id` click events for Aviasales, Google Flights, and Skyscanner, plus conversion webhook attribution (`POST /api/v1/affiliate/conversions`) and live RPM tracking.
  - End-to-end **Price Drop Alerts** (`POST /api/v1/alerts`, `POST /api/v1/alerts/check`) integrated into the daily Prefect flow.

## Quick Start (Docker Compose)

1. Copy `.env.example` to `.env` and set a strong local `POSTGRES_PASSWORD`.
2. Start the full stack:

   ```cmd
   docker compose up --build -d
   ```

3. If upgrading an existing PostgreSQL volume, apply migrations `003` and `004`:

   ```cmd
   docker compose exec -T postgres psql -U smartfare -d smartfare < db\migrations\003_fx_rates.sql
   docker compose exec -T postgres psql -U smartfare -d smartfare < db\migrations\004_click_events_and_alerts.sql
   ```

4. Populate FX rates, SerpApi snapshots, and build/test the dbt warehouse:

   ```cmd
   docker compose exec ingestion-runner python -m ingestion.partitions
   docker compose exec ingestion-runner python -m ingestion.fx_rates
   docker compose exec ingestion-runner python -m ingestion.import_serpapi_snapshots /app/data/cai_international_flights.csv /app/data/destination_hotels.csv
   docker compose exec api dbt run --full-refresh --project-dir /app/transform --profiles-dir /app/transform
   docker compose exec api dbt test --project-dir /app/transform --profiles-dir /app/transform
   ```

5. *(Optional)* Generate the 3-year synthetic historical datasets from the SerpApi baselines (`scripts/build_historical_flights.py` and `scripts/build_historical_hotels.py`), train the ML registry inside Docker, and reload FastAPI:

   ```cmd
   python scripts\build_historical_flights.py --baseline data\cai_international_flights.csv --out data\historical_flights_3yrs.csv --rows-per-year 30000
   python scripts\build_historical_hotels.py --baseline data\destination_hotels.csv --out data\historical_hotels_3yrs.csv --rows-per-year 30000
   docker compose exec ingestion-runner python -m ml.train
   curl -X POST http://localhost:8000/api/v1/models/reload
   ```

6. Open the services:
   - **Streamlit Dashboard:** <http://localhost:8501>
   - **FastAPI OpenAPI Docs:** <http://localhost:8000/docs>
   - **Prefect UI:** <http://localhost:4200>

## Dashboard Tabs

1. **Booking Advisor:** Book Now / Wait / Neutral recommendations, tracked affiliate deep-link buttons, Plotly 25th–75th percentile Booking Curve, Flight + Hotel Total Trip Cost Heatmap, and USD-normalized cheapest travel windows.
2. **Price Alerts & Affiliate:** Create/deactivate target-price alerts, trigger immediate alert evaluation, inspect tracked `sub_id` click logs, simulate partner conversion webhooks, and track Commission USD & RPM.
3. **Observed Flights:** Interactive Plotly trend charts of provider observations collected into Gold marts.
4. **SerpApi Flights:** One-time Google Flights snapshot mart exploration by Cairo route.
5. **SerpApi Hotels:** One-time Google Hotels snapshot mart exploration with lowest nightly rates by destination.
6. **ML Registry & Forecasts:** Live vs. synthetic promotion gate status, walk-forward backtest scorecard (Brier, ROC-AUC, P50 MAE, Simulated Regret), and FastAPI-served flight & hotel lead-time curves.

## API Endpoints

Interactive schema documentation is available at <http://localhost:8000/docs>:

| Endpoint | Description |
| :--- | :--- |
| `GET /health` | Service liveness check |
| `GET /api/v1/routes` | Tracked routes with price observations |
| `GET /api/v1/routes/{route_id}/departures` | Available departure/return date pairs |
| `GET /api/v1/routes/{route_id}/price-history` | Observed min and median fare time series |
| `GET /api/v1/routes/{route_id}/recommendation` | Phase 1 statistical or promoted ML **BOOK_NOW / WAIT / NEUTRAL** decision |
| `GET /api/v1/routes/{route_id}/cheapest-windows` | USD-normalized Flight + Hotel travel window rankings |
| `GET /api/v1/routes/{route_id}/booking-curve` | 25th–75th percentile booking curve by lead days |
| `GET /api/v1/routes/{route_id}/trip-heatmap` | Combined Flight + Hotel USD matrix across stay lengths |
| `GET /api/v1/routes/{route_id}/ml-decision` | Direct classifier & quantile regressor decision output |
| `GET /api/v1/models/status` & `POST /api/v1/models/reload` | Inspect scorecards and hot-reload `.joblib` artifacts |
| `GET /api/v1/predict/flight` & `/predict/hotel` | FastAPI lead-time curve predictions |
| `GET /api/v1/redirect/book` | Log `sub_id` click event and `302` redirect to booking partner |
| `POST /api/v1/affiliate/conversions` & `GET /api/v1/affiliate/stats` | Attribute affiliate commission by `sub_id` and compute RPM |
| `POST /api/v1/alerts`, `GET /api/v1/alerts`, `DELETE /api/v1/alerts/{id}`, `POST /api/v1/alerts/check` | Manage and evaluate target-price alerts |
| `GET /api/v1/serpapi/flights` & `/serpapi/hotels` | SerpApi snapshot marts |

## Prefect Orchestration

- **`smartfare-daily-ingest` (02:00 UTC daily):** `maintain-raw-partitions` → `ingest-daily-fx-rates` → `collect-flight-prices` → `collect-hotel-prices` → `dbt run` → `dbt test` → `check-price-drop-alerts`, with Slack/Telegram failure alerts.
- **`smartfare-weekly-train` (04:00 UTC Sunday):** Trains warehouse & prototype ML bundles, writes `scorecard.json`, and calls `POST /api/v1/models/reload`.

## Testing & CI

Run the automated test suite and linter inside Docker or locally:

```cmd
docker compose exec api pip install pytest pytest-asyncio ruff
docker compose exec api ruff check api dashboard ingestion ml tests
docker compose exec api pytest -v
```

GitHub Actions (`.github/workflows/ci.yml`) runs `ruff check`, `pytest -v`, and `dbt parse` automatically on every push and pull request.

## Data Provenance & Safety

- Mock and 3-year synthetic datasets (`data/historical_flights_3yrs.csv`, `data/historical_hotels_3yrs.csv`, `data/synthetic_*`) are strictly for engineering, backtesting, and demonstration—never present them as live market forecasts.
- Live warehouse ML promotion remains gated until real daily provider history passes walk-forward out-of-sample thresholds.
- Never commit `.env`, API tokens, webhook URLs, binary `.joblib` artifacts, or large generated CSVs.

See [WAREHOUSE_DESIGN.md](WAREHOUSE_DESIGN.md) and [INSTRUCTOR_DEMO.md](INSTRUCTOR_DEMO.md) for schema details and the live demonstration script.
