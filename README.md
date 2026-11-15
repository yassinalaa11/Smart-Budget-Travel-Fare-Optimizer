# SmartFare

SmartFare is a Docker-based prototype for collecting and exploring flight prices, with an initial foundation for hotel-price data. It uses PostgreSQL for storage, Prefect for orchestration, FastAPI for the API, Streamlit for the dashboard, and dbt for transformations.

## Current status

- Flight ingestion supports a mock provider and configured external providers.
- Raw provider responses and ingestion request logs are stored in PostgreSQL.
- dbt builds staging, quality, and flight analytics views.
- FastAPI and Streamlit provide access to the current flight-price results.
- Hotel import and transformation code is present, but requires a real, permitted hotel-price CSV source.

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
