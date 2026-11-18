# Instructor demo checklist

## 1. Show the ETL path

- Explain that flight and hotel sources land unchanged in Bronze (`raw.api_responses`).
- Run `ingestion.import_serpapi_snapshots` and show its inserted/duplicate counts.
- Run dbt and show the staging, intermediate, dimension, and fact models completing.

## 2. Show the warehouse design

- Open `WAREHOUSE_DESIGN.md` and walk through Bronze → Silver → Gold.
- In PostgreSQL, show `analytics_marts.dim_date`, `dim_route`, `dim_hotel`, `fct_flight_price_observations`, and `fct_hotel_price_observations`.
- Show that the loaded flight and hotel facts have no missing route/hotel or date keys.

## 3. Show the consumers and explain limits

- Open the Streamlit dashboard and show the observed prices plus the SerpApi flight/hotel sections.
- Explain that the prediction tab uses synthetic data for a classroom prototype.
- State that the SerpApi dataset is a one-time snapshot; historical collection is needed before claiming reliable price timing.
