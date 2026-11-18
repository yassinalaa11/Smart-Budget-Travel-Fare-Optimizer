# SmartFare

SmartFare is a Docker-based ETL and travel-price analytics prototype. PostgreSQL stores raw provider responses and the Gold warehouse, Prefect orchestrates provider ingestion, dbt transforms data, FastAPI serves warehouse results, and Streamlit presents the dashboard.

## Current status

- Flight ingestion supports a mock provider and configured external providers.
- Raw provider responses and ingestion request logs are stored in PostgreSQL.
- Bronze stores raw provider payloads and request audit records in PostgreSQL.
- Silver dbt models parse and normalize flight and hotel data, deduplicate flights, and flag quality issues.
- Gold dbt models build the `dim_date`, `dim_route`, and `dim_hotel` dimensions and the flight and hotel fact tables.
- FastAPI and Streamlit read the curated warehouse results.

## Instructor demo: synthetic prediction prototype

The Streamlit dashboard has four sections. **Observed flight prices** displays the small provider sample stored by the ingestion pipeline. **SerpApi flights** displays `data/cai_international_flights.csv` as a separate one-time offer snapshot. **SerpApi hotels** displays `data/destination_hotels.csv`, another one-time snapshot, with hotel listings and EGP prices. **Prediction demo** trains a Random Forest model on the supplied synthetic flight and hotel CSVs and compares its mean absolute error (MAE) with a simple median-price baseline using a chronological holdout.

The SerpApi snapshots were collected at one timestamp. The flight file contains flight offers and the hotel file contains hotel prices for a fixed check-in/check-out window. Neither file represents historical price changes, and neither is used to train the synthetic prediction models. Keep the API key out of the CSV and repository. Sources: [SerpApi Google Flights API](https://serpapi.com/google-flights-api) and [Google Hotels API](https://serpapi.com/google-hotels-api).

The flight demo uses `data/synthetic_flights_calibrated.csv`. Its synthetic values were rescaled to match the min/median/max of a small Travelpayouts sample for CAI-DXB and CAI-IST. It is still synthetic, and the calibration sample is too small to validate a useful travel forecast. The hotel demo uses the supplied synthetic hotel files and has no real-price calibration. Present the output as a functioning ML prototype, not as booking advice or proven accuracy. See `data/synthetic_flights_calibration_notes.md` for provenance.

To rebuild after pulling the project or adding these files:

```powershell
docker compose up --build -d
```

Then open <http://localhost:8501> and choose **Prediction demo**. The dashboard container mounts `data/` read-only so the example datasets remain unchanged.

Mock and synthetic data are for development only. They must not be presented as real prices or used to claim forecast accuracy. Real provider coverage is currently limited, and this project does not yet have two years of real historical observations. Check each data provider's terms before collecting or storing data.

## Run with Docker Compose

1. Copy `.env.example` to `.env`.
2. Set a strong, local `POSTGRES_PASSWORD`. Add provider credentials only if you have them.
3. Start the services:

   ```powershell
   docker compose up --build -d
   ```

4. Open the services:
   - Streamlit dashboard: <http://localhost:8501>
   - FastAPI documentation: <http://localhost:8000/docs>
   - Prefect UI: <http://localhost:4200>

The default provider is `mock`, so the app can start without provider credentials. Real API ingestion requires valid credentials and is subject to provider availability and usage terms.

## ETL and data warehouse

The warehouse follows the instructor's Bronze → Silver → Gold layout:

1. **Bronze:** `raw.api_responses` keeps the provider payload, request parameters, response status, payload hash, and ingestion timestamps.
2. **Silver:** `analytics_staging` parses provider JSON into typed columns; `analytics_intermediate` deduplicates flight offers and marks quality issues.
3. **Gold:** `analytics_marts` contains `dim_date`, `dim_route`, `dim_hotel`, `fct_flight_price_observations`, and `fct_hotel_price_observations`.

Fact table grains and dimension keys are documented in [WAREHOUSE_DESIGN.md](WAREHOUSE_DESIGN.md).

After starting the services, load the committed SerpApi snapshots into Bronze and rebuild the warehouse:

```powershell
docker compose exec ingestion-runner python -m ingestion.import_serpapi_snapshots /app/data/cai_international_flights.csv /app/data/destination_hotels.csv
docker compose exec api dbt run --project-dir /app/transform --profiles-dir /app/transform
```

The importer skips exact duplicate payloads when rerun. Check the raw source counts:

```powershell
docker compose exec postgres psql -U smartfare -d smartfare -c "SELECT endpoint, count(*) FROM raw.api_responses WHERE provider='serpapi_snapshot' GROUP BY endpoint;"
```

Check the Gold fact-to-dimension keys:

```powershell
docker compose exec postgres psql -U smartfare -d smartfare -c "SELECT count(*) AS fact_rows, count(*) FILTER (WHERE route_key IS NULL) AS missing_route_key, count(*) FILTER (WHERE observed_date_key IS NULL) AS missing_observed_date_key, count(*) FILTER (WHERE departure_date_key IS NULL) AS missing_departure_date_key FROM analytics_marts.fct_flight_price_observations;"
docker compose exec postgres psql -U smartfare -d smartfare -c "SELECT count(*) AS fact_rows, count(*) FILTER (WHERE hotel_key IS NULL) AS missing_hotel_key, count(*) FILTER (WHERE observed_date_key IS NULL) AS missing_observed_date_key, count(*) FILTER (WHERE checkin_date_key IS NULL) AS missing_checkin_date_key FROM analytics_marts.fct_hotel_price_observations;"
```

The SerpApi files are single snapshots, not historical price series. They demonstrate ETL and support descriptive analysis but cannot train a trustworthy best-time-to-travel model. The prediction demo continues to use clearly labeled synthetic data.

## Run transformations

```powershell
docker compose exec api dbt run --project-dir /app/transform --profiles-dir /app/transform
```

## Hotel CSV import

Place a permitted hotel-price CSV under `data/` and run the importer with its container path, for example:

```powershell
docker compose run --rm ingestion-runner python -m ingestion.import_hotels /app/data/hotel_prices.csv
```

The CSV must follow the importer schema. `data/hotel_prices_template.csv` documents the expected columns; it contains no observations.

## Credentials and generated files

Keep `.env` local. Never commit API tokens, passwords, private datasets, or generated outputs. `.env.example` contains placeholders only. dbt build outputs and logs are excluded from Git.
