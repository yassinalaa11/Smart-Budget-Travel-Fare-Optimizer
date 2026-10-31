# SmartFare dbt project

This project reads successful Amadeus and mock responses from `raw.api_responses`,
builds `analytics_staging.stg_flight_responses`, removes exact repeated offers in
`analytics_intermediate.int_deduplicated_flights`, flags low USD prices and spikes in
`analytics_intermediate.int_flight_price_quality`, exposes flagged rows in
`analytics_marts.quarantined_observations`, and aggregates unflagged offers into
`analytics_marts.fct_flight_price_observations`. Spike detection waits for three
observation days so it has a stable comparison baseline. Currency conversion is not
applied; the fact model keeps currencies separate. Mock rows remain identifiable
through `is_demo`; they are synthetic and must not be used as live fares.

Run the model in the API container after rebuilding it:

```powershell
docker compose up --build -d api
docker compose exec api dbt run --project-dir /app/transform --profiles-dir /app/transform
```

The profile reads connection values from container environment variables; no database
password is stored in the dbt profile file.
