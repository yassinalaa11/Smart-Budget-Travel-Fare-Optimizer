# SmartFare warehouse design

## Architecture mapped to the instructor's diagram

| Layer | SmartFare implementation | Purpose |
| --- | --- | --- |
| Source systems | SerpApi flight and hotel snapshots; configured flight providers; mock provider | External feeds and clearly labeled demo data |
| Bronze | `raw.api_responses`, `public.ingestion_request_log` | Preserve source JSON, request parameters, status, hashes, and ingestion timestamps |
| Silver | `analytics_staging`, `analytics_intermediate` | Parse JSON, normalize fields, deduplicate offers, and flag invalid prices |
| Gold | `analytics_marts` | Query-ready fact tables and shared dimensions for analytics and the API |
| Consumers | FastAPI, Streamlit dashboard, prediction demo | Read curated warehouse outputs |

## Gold star schema

### `fct_flight_price_observations`

**Grain:** one provider, route, departure/return-date combination, observation day,
cabin class, and currency. The row contains minimum and median prices, offer and
response counts, and demo-data flags. One-way flights have a null return date.

Foreign keys: `route_key`, `observed_date_key`, `departure_date_key`, and nullable
`return_date_key`. Existing natural identifiers and price columns remain available
for the API.

### `fct_hotel_price_observations`

**Grain:** one source, hotel, stay window, observation day, guest count, and currency.
The row contains minimum/median stay total, observation count, and the source identity.

Foreign keys: `hotel_key`, `observed_date_key`, `checkin_date_key`, and
`checkout_date_key`.

### Dimensions

- `dim_date`: continuous calendar covering observed, departure, return, check-in, and
  check-out dates present in Silver. Includes weekday, weekend, month, quarter, year,
  and season attributes. Holiday fields are not populated because no verified holiday
  source is configured.
- `dim_route`: one row per observed origin/destination pair, with a stable hash key.
- `dim_hotel`: one row per source and hotel identifier, with a stable hash key and
  descriptive hotel/market attributes.

The hash keys are deterministic warehouse keys for this prototype. The models rebuild
the Gold tables from the available raw data on each `dbt run`; they are not yet
incremental or slowly changing dimensions.

## Data caveats

The imported SerpApi files represent one snapshot date and one hotel stay window.
They demonstrate the ETL path and support descriptive analysis, but do not form a
historical series for learning when prices will be lower. Synthetic prediction data
must remain labeled synthetic. Mock data must remain labeled demo data.
