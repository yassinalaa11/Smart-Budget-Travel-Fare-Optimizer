# SmartFare

SmartFare is a Docker-based prototype for collecting and exploring flight prices, with an initial foundation for hotel-price data. It uses PostgreSQL for storage, Prefect for orchestration, FastAPI for the API, Streamlit for the dashboard, and dbt for transformations.

## Current status

- Flight ingestion supports a mock provider and configured external providers.
- Raw provider responses and ingestion request logs are stored in PostgreSQL.
- dbt builds staging, quality, and flight analytics views.
- FastAPI and Streamlit provide access to the current flight-price results.
- Hotel import and transformation code is present, but requires a real, permitted hotel-price CSV source.

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
