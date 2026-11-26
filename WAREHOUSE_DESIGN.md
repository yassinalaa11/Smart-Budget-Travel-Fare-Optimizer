# SmartFare Warehouse Design

SmartFare implements a PostgreSQL + dbt Medallion architecture (**Bronze → Silver → Gold**) paired with operational tables for ingestion auditing, FX conversion, affiliate attribution, and price-drop alerting.

## 1. Operational & Bronze Tables

| Table | Schema | Partitioning / Keys | Purpose |
| :--- | :--- | :--- | :--- |
| `raw.api_responses` | `raw` | Range-partitioned monthly on `ingested_date` (`api_responses_yYYYYmMM` + `api_responses_default`) | Immutable raw JSONB payloads from flight, hotel, FX, and snapshot sources with SHA-256 `payload_hash` and `dag_run_id` |
| `public.ingestion_request_log` | `public` | PK `request_id` | Tracks latency, HTTP status, record count, and outcome (`succeeded`, `http_error`, `network_error`) per API call |
| `public.fx_rates` | `public` | PK `(rate_date, base_currency, quote_currency)` | Daily USD-base exchange rates (`open.er-api.com`) used to normalize EGP/EUR/GBP/AED/SAR offers to USD |
| `public.click_events` | `public` | PK `click_id`, unique `sub_id` | Outbound affiliate deep-link clicks, quoted fares, partner conversion webhooks, booking value, and commission USD |
| `public.price_alerts` | `public` | PK `alert_id` | User target fares (`target_price_usd`), cooldown tracking (`last_triggered_at`), and observed price snapshots |

## 2. Silver Layer (Staging & Intermediate Views)

- **`analytics_staging.stg_flight_responses`:** Flattens raw flight JSONB arrays across `mock`, `amadeus`, `travelpayouts`, and `serpapi_snapshot` into typed itinerary offers with deterministic `offer_hash`.
- **`analytics_staging.stg_hotel_price_observations`:** Flattens raw hotel JSONB arrays across `historical_csv_hotels`, `serpapi_snapshot`, `mock_hotels`, `serpapi_hotels`, and `amadeus_hotels`.
- **`analytics_staging.stg_fx_rates`:** Stages positive USD-base rates from `public.fx_rates` with a guaranteed fallback row (`USD = 1.000000`).
- **`analytics_intermediate.int_deduplicated_flights`:** Two-stage deduplication:
  1. Exact deduplication per `(provider, route, departure_date, return_date, observed_date, cabin_class, currency_code, payload_hash, offer_hash)`.
  2. Codeshare/itinerary deduplication keeping the lowest fare per matching `(departure_time, arrival_time, duration_min, stops)` window.
- **`analytics_intermediate.int_flight_price_quality`:** Attaches nearest-prior USD FX rate (`total_price_usd`) and flags rows with `quality_reason`:
  - `non_positive_price` (`total_price <= 0`)
  - `return_before_departure`
  - `price_usd_below_15`
  - `price_spike_over_4x_stable_baseline`

## 3. Gold Layer (Star Schema, Feature Mart & SLA Views)

### Dimensions

- **`analytics_marts.dim_date`** (grain: 1 row per calendar `date_key`):
  - Calendar attributes (`day_of_week`, `day_of_year`, `is_weekend`, `month_num`, `quarter_num`, `year_num`, `season_name`).
  - Holiday and demand flags (`is_origin_public_holiday` for Egypt, `is_destination_public_holiday`, `school_break_flag`, `days_to_nearest_holiday`).
- **`analytics_marts.dim_route`** (grain: 1 row per `route_id` / surrogate `route_key`).
- **`analytics_marts.dim_hotel`** (grain: 1 row per `(source, hotel_id)` / surrogate `hotel_key`).

### Incremental Fact Tables

- **`analytics_marts.fct_flight_price_observations`**
  - **Materialization:** `incremental` (`delete+insert` on `flight_observation_key`, 3-day lookback window).
  - **Grain:** `(provider, route_id, departure_date, return_date, observed_date, cabin_class, currency_code)`.
  - **Measures:** `min_total_price`, `median_total_price`, `min_total_price_usd`, `median_total_price_usd`, `cheapest_direct_price(_usd)`, `cheapest_1stop_price(_usd)`, `min_duration_minutes`, `active_carrier_count` (distinct carriers within 15% of daily minimum), `offer_count`, `response_count`, `contains_demo_data`, `all_data_is_demo`.
  - **Foreign Keys:** `route_key → dim_route`, `observed_date_key / departure_date_key / return_date_key → dim_date`.

- **`analytics_marts.fct_hotel_price_observations`**
  - **Materialization:** `incremental` (`delete+insert` on `hotel_observation_key`, 3-day lookback window).
  - **Grain:** `(source, city_iata, hotel_id, checkin_date, checkout_date, observed_date, guests, currency_code)`.
  - **Measures:** `min_total_price`, `median_total_price`, `fx_rate_to_usd`, `min_total_price_usd`, `median_total_price_usd`, `observation_count`.
  - **Foreign Keys:** `hotel_key → dim_hotel`, `observed_date_key / checkin_date_key / checkout_date_key → dim_date`.

### Quality, ML Feature & Operations Marts

- **`analytics_marts.quarantined_observations`:** Preserves every row rejected by `int_flight_price_quality` with its `quality_reason` for auditability.
- **`analytics_marts.ml_features_flight_pricing`** (grain: `flight_observation_key`):
  - Lead-time & calendar features: `days_to_departure`, `lead_time_bucket`, `is_friday_sunday_pair`, `is_origin_public_holiday`, `is_destination_public_holiday`, `school_break_flag`, `days_to_nearest_holiday`, cyclical `sin`/`cos` encodings (`day_of_year_sin/cos`, `day_of_week_sin/cos`).
  - Rolling & momentum features (partitioned per route/departure, ordered strictly by `observed_date` to prevent leakage): `rolling_mean_7d`, `rolling_mean_14d`, `rolling_std_7d`, `price_velocity_3d`, `price_velocity_7d`, `z_score_vs_route_horizon`, `drawdown_from_peak`, `markup_over_historical_min`, `direct_vs_1stop_spread_pct`, `active_carrier_count`.
- **`analytics_marts.ops_ingestion_completeness`:** Daily provider SLA view computing `routes_with_valid_offers`, `tracked_route_count`, `route_coverage_pct`, `meets_95pct_sla`, and succeeded/failed request counts.

## 4. Data Quality Gates (`dbt test`)

`transform/models/marts/schema.yml` enforces `not_null`, `unique`, and foreign-key `relationships` across all dimensions, incremental fact keys, holiday indicators, cyclical ML encodings, and SLA completeness metrics. Every daily Prefect run executes `dbt run` followed immediately by `dbt test`.
