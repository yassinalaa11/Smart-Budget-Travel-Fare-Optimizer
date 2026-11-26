# SmartFare

SmartFare is a Docker-based ETL, data warehouse, and travel-price analytics platform. PostgreSQL stores raw provider responses, daily FX rates, and the Gold star schema; Prefect orchestrates daily partition maintenance, ingestion, transformations, and data quality tests; dbt builds and validates Silver and Gold models; FastAPI serves price history and booking recommendations; and Streamlit presents the interactive dashboard.

## Current status

- **Automated daily ingestion (flights, hotels, FX):** Supports `mock`, `amadeus`, and `travelpayouts` for flights; `mock`, `serpapi`, and `amadeus` for hotels; and daily USD-base exchange rates (`open.er-api.com`) stored in `public.fx_rates`.
- **Resilience & observability:** Exponential backoff with jitter on HTTP `429` / `5xx` and transient network errors (`ingestion/http_retry.py`), request audit logging in `public.ingestion_request_log`, automated monthly partition maintenance on `raw.api_responses`, and Slack/Telegram failure alerts (`ingestion/alerts.py`).
- **Bronze layer:** `raw.api_responses` (range-partitioned by `ingested_date`) preserves raw provider payloads, request parameters, HTTP status codes, SHA-256 payload hashes, and ingestion timestamps.
- **Silver layer:** `analytics_staging` parses flight and hotel JSON (live, mock, historical CSV, and SerpApi snapshots) into typed columns; `analytics_intermediate` deduplicates flight offers and flags low-price anomalies and spikes.
- **Gold layer & quality gates:** `analytics_marts` builds `dim_date`, `dim_route`, `dim_hotel`, `fct_flight_price_observations`, `fct_hotel_price_observations`, `quarantined_observations`, and the time-series feature table `ml_features_flight_pricing`, validated by 28 dbt tests (`not_null`, `unique`, and foreign-key `relationships`).
- **Decision API & dashboard:** FastAPI serves price history, SerpApi snapshots, Phase 1 **Book Now / Wait / Neutral** recommendations, and combined Flight + Hotel cheapest-window rankings. Streamlit presents a five-tab dashboard.

## Dashboard & instructor demo

The Streamlit dashboard (`http://localhost:8501`) has five sections:

1. **Booking Advisor:** Queries `/api/v1/routes/{route_id}/recommendation` and `/api/v1/routes/{route_id}/cheapest-windows` to display color-coded **BOOK NOW / WAIT / NEUTRAL** baseline signals, route percentiles, confidence level, and lowest-cost travel windows.
2. **Observed flight prices:** Displays the provider sample collected by the ingestion pipeline and served from Gold marts.
3. **SerpApi flights:** Displays `data/cai_international_flights.csv` via the warehouse marts as a separate one-time offer snapshot.
4. **SerpApi hotels:** Displays `data/destination_hotels.csv` via the warehouse marts as a separate one-time snapshot with hotel listings and EGP prices.
5. **Prediction demo:** Trains a Random Forest model on the supplied synthetic flight and hotel CSVs and compares its mean absolute error (MAE) with a simple median-price baseline using a chronological holdout.

The SerpApi snapshots were collected at one timestamp. The flight file contains flight offers and the hotel file contains hotel prices for a fixed check-in/check-out window. Neither file represents historical price changes, and neither is used to train the synthetic prediction models. Keep API keys out of CSVs and the repository. Sources: [SerpApi Google Flights API](https://serpapi.com/google-flights-api) and [Google Hotels API](https://serpapi.com/google-hotels-api).

The flight demo uses `data/synthetic_flights_calibrated.csv`. Its synthetic values were rescaled to match the min/median/max of a small Travelpayouts sample for CAI-DXB and CAI-IST. It is still synthetic, and the calibration sample is too small to validate a useful travel forecast. The hotel demo uses the supplied synthetic hotel files and has no real-price calibration. Present the output as a functioning ML prototype, not as booking advice or proven accuracy. See `data/synthetic_flights_calibration_notes.md` for provenance.

Mock and synthetic data are for development only. They must not be presented as real prices or used to claim forecast accuracy. Check each data provider's terms before collecting or storing data.

## Run with Docker Compose

1. Copy `.env.example` to `.env`.
2. Set a strong, local `POSTGRES_PASSWORD`.
3. Optionally configure live providers and failure alerts in `.env`:
   - `SMARTFARE_PROVIDER`: `mock` (default), `travelpayouts`, or `amadeus`
   - `SMARTFARE_HOTEL_PROVIDER`: `mock` (default), `serpapi`, or `amadeus`
   - Provider keys: `TRAVELPAYOUTS_TOKEN`, `AMADEUS_CLIENT_ID`, `AMADEUS_CLIENT_SECRET`, `SERPAPI_API_KEY`
   - Optional alerts: `SLACK_WEBHOOK_URL`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`
4. Start the services:

   ```cmd
   docker compose up --build -d
   ```

5. If upgrading an existing PostgreSQL volume, apply the FX rates migration once:

   ```cmd
   docker compose exec -T postgres psql -U smartfare -d smartfare < db\migrations\003_fx_rates.sql
   ```

6. Open the services:
   - Streamlit dashboard: <http://localhost:8501>
   - FastAPI documentation: <http://localhost:8000/docs>
   - Prefect UI: <http://localhost:4200>

Default providers are `mock` for both flights and hotels so the entire stack starts without external credentials.

## Daily Prefect pipeline

The scheduled Prefect flow (`smartfare-daily-ingest`) runs daily at **02:00 UTC** and executes six tasks in sequence:

1. `maintain-raw-partitions` — creates upcoming monthly partitions (`raw.api_responses_yYYYYmMM`) and migrates any matching rows out of `raw.api_responses_default`.
2. `ingest-daily-fx-rates` — fetches daily USD-base exchange rates into `public.fx_rates` and `raw.api_responses`.
3. `collect-flight-prices` — runs the configured flight provider (`mock`, `travelpayouts`, or `amadeus`).
4. `collect-hotel-prices` — runs the configured hotel provider (`mock`, `serpapi`, or `amadeus`).
5. `build-silver-and-gold-warehouse` — runs `dbt run`.
6. `test-warehouse-quality` — runs `dbt test` (28 schema & referential integrity tests).

If any task fails, the flow dispatches an alert to Slack and/or Telegram when configured.

Trigger the flow manually from the Prefect UI or terminal:

```cmd
docker compose exec ingestion-runner prefect deployment run "smartfare-daily-ingest/smartfare-daily-ingest"
```

Run individual ingestion utilities directly when needed:

```cmd
docker compose exec ingestion-runner python -m ingestion.partitions
docker compose exec ingestion-runner python -m ingestion.fx_rates
docker compose exec ingestion-runner python -m ingestion.collector
docker compose exec ingestion-runner python -m ingestion.hotel_collector
```

The committed SerpApi CSV snapshots are a separate, one-time import; the daily flow does not re-import static snapshots.

## ETL and data warehouse

The warehouse follows a Bronze → Silver → Gold layout:

1. **Bronze:** `raw.api_responses` (monthly range-partitioned) keeps raw payloads, request parameters, status codes, SHA-256 hashes, and ingestion timestamps; `public.ingestion_request_log` tracks latency and outcomes; `public.fx_rates` stores daily exchange rates.
2. **Silver:** `analytics_staging` (`stg_flight_responses`, `stg_hotel_price_observations`) parses JSON into typed columns; `analytics_intermediate` (`int_deduplicated_flights`, `int_flight_price_quality`) deduplicates flight offers and flags quality anomalies.
3. **Gold:** `analytics_marts` contains `dim_date`, `dim_route`, `dim_hotel`, `fct_flight_price_observations`, `fct_hotel_price_observations`, `quarantined_observations`, and `ml_features_flight_pricing`.

Fact table grains and dimension keys are documented in [WAREHOUSE_DESIGN.md](WAREHOUSE_DESIGN.md).

After starting the services, load the committed SerpApi snapshots into Bronze and build/test the warehouse:

```cmd
docker compose exec ingestion-runner python -m ingestion.import_serpapi_snapshots /app/data/cai_international_flights.csv /app/data/destination_hotels.csv
docker compose exec api dbt run --project-dir /app/transform --profiles-dir /app/transform
docker compose exec api dbt test --project-dir /app/transform --profiles-dir /app/transform
```

The snapshot importer skips exact duplicate payloads when rerun. Verify raw counts, partitions, FX rates, and Gold keys:

```cmd
docker compose exec postgres psql -U smartfare -d smartfare -c "SELECT endpoint, count(*) FROM raw.api_responses WHERE provider='serpapi_snapshot' GROUP BY endpoint;"
docker compose exec postgres psql -U smartfare -d smartfare -c "SELECT inhrelid::regclass AS partition_name FROM pg_inherits WHERE inhparent = 'raw.api_responses'::regclass ORDER BY 1;"
docker compose exec postgres psql -U smartfare -d smartfare -c "SELECT rate_date, base_currency, quote_currency, rate FROM public.fx_rates WHERE quote_currency IN ('EGP','EUR','GBP','AED','SAR') ORDER BY quote_currency;"
docker compose exec postgres psql -U smartfare -d smartfare -c "SELECT count(*) AS fact_rows, count(*) FILTER (WHERE route_key IS NULL) AS missing_route_key, count(*) FILTER (WHERE observed_date_key IS NULL) AS missing_observed_date_key, count(*) FILTER (WHERE departure_date_key IS NULL) AS missing_departure_date_key FROM analytics_marts.fct_flight_price_observations;"
docker compose exec postgres psql -U smartfare -d smartfare -c "SELECT count(*) AS fact_rows, count(*) FILTER (WHERE hotel_key IS NULL) AS missing_hotel_key, count(*) FILTER (WHERE observed_date_key IS NULL) AS missing_observed_date_key, count(*) FILTER (WHERE checkin_date_key IS NULL) AS missing_checkin_date_key FROM analytics_marts.fct_hotel_price_observations;"
```

## API endpoints

All endpoints are documented interactively at <http://localhost:8000/docs>:

- `GET /health` — service health check.
- `GET /api/v1/routes` — list routes with price observations.
- `GET /api/v1/routes/{route_id}/departures` — list departure/return date options for a route.
- `GET /api/v1/routes/{route_id}/price-history` — observed minimum and median fare history.
- `GET /api/v1/routes/{route_id}/recommendation` — Phase 1 baseline **BOOK_NOW / WAIT / NEUTRAL** signal with route quantiles and confidence.
- `GET /api/v1/routes/{route_id}/cheapest-windows` — lowest-cost travel windows combining flights and matched hotels.
- `GET /api/v1/serpapi/flights` — SerpApi flight snapshot mart results.
- `GET /api/v1/serpapi/hotels` — SerpApi hotel snapshot mart results.

## Run transformations & quality tests

```cmd
docker compose exec api dbt run --project-dir /app/transform --profiles-dir /app/transform
docker compose exec api dbt test --project-dir /app/transform --profiles-dir /app/transform
```

## Hotel CSV import

Place a permitted hotel-price CSV under `data/` and run the importer with its container path:

```cmd
docker compose run --rm ingestion-runner python -m ingestion.import_hotels /app/data/hotel_prices.csv
```

The CSV must follow the importer schema documented in `data/hotel_prices_template.csv`.

## Credentials and generated files

Keep `.env` local. Never commit API tokens, passwords, webhooks, private datasets, or generated outputs. `.env.example` contains placeholders only. dbt build outputs and logs are excluded from Git.