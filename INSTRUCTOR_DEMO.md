# SmartFare — Instructor Demo Walkthrough

A 10–15 minute live walkthrough demonstrating SmartFare end-to-end: ETL orchestration, Bronze → Silver → Gold warehouse testing, decision-focused ML with promotion gates, interactive Plotly analytics, affiliate click attribution, and automated CI tests.

## Pre-Demo Checklist (2 Minutes Before Presenting)

Run inside `C:\Users\mohamed\SmartFare>`:

```cmd
docker compose ps
curl http://localhost:8000/health
docker compose exec api dbt test --project-dir /app/transform --profiles-dir /app/transform
```

Confirm all five containers (`postgres`, `prefect`, `api`, `ingestion-runner`, `streamlit`) are running, `/health` returns `{"status":"ok"}`, and all dbt tests pass.

Open three browser tabs side-by-side:
1. **Streamlit Dashboard:** <http://localhost:8501>
2. **FastAPI Swagger UI:** <http://localhost:8000/docs>
3. **Prefect UI:** <http://localhost:4200>

---

## Step 1 — Data Engineering & Medallion Warehouse (3 Minutes)

1. Show **Prefect UI** (`http://localhost:4200`) and explain the scheduled `smartfare-daily-ingest` flow:
   - Monthly partition maintenance (`raw.api_responses_yYYYYmMM`)
   - Daily USD FX ingestion (`public.fx_rates`)
   - Flight & Hotel collection with exponential backoff retries (`ingestion/http_retry.py`)
   - `dbt run` + `dbt test` quality gates
   - Nightly price-drop alert evaluation + Slack/Telegram failure alerting
2. Run a quick SQL verification in terminal to show live partitions, FX normalization, and Gold feature engineering:

   ```cmd
   docker compose exec postgres psql -U smartfare -d smartfare -c "SELECT inhrelid::regclass AS partition_name FROM pg_inherits WHERE inhparent = 'raw.api_responses'::regclass ORDER BY 1;"
   docker compose exec postgres psql -U smartfare -d smartfare -c "SELECT route_id, departure_date, currency_code, min_total_price, min_total_price_usd, z_score_vs_route_horizon, days_to_nearest_holiday FROM analytics_marts.ml_features_flight_pricing LIMIT 5;"
   ```

---

## Step 2 — Booking Advisor, Plotly Analytics & FX Pairing (3 Minutes)

1. Open **Streamlit → Booking Advisor** and select **`CAI → DXB`**.
2. Toggle between a near-term date (`2026-11-29`, 4 days out → **`BOOK_NOW`** advance-purchase cliff) and a far-out date (`2027-01-14`, 50 days out → **`NEUTRAL`** near route median).
3. Point out the two side-by-side Plotly visualizations:
   - **Historical Booking Curve:** Shaded 25th–75th percentile fare cone as `days_to_departure` counts down, with a red diamond marking the selected trip's USD quote.
   - **Total Trip Cost Heatmap:** Combined Flight + Hotel USD total across departure dates and stay lengths (3, 5, 7, 10 nights).
4. Scroll to **Cheapest Travel Windows** showing EGP flight fares paired with destination hotels and normalized to USD via `public.fx_rates`.

---

## Step 3 — Monetization Loop: Affiliate Attribution & Price Alerts (3 Minutes)

1. Still in **Booking Advisor**, click **Book on Aviasales** (or run the redirect `curl` below) to show the `302` redirect logging a unique `sub_id`:

   ```cmd
   curl -i "http://localhost:8000/api/v1/redirect/book?route_id=CAI-DXB&departure_date=2027-01-14&partner=aviasales&quoted_price=193.42&quoted_currency=USD"
   ```

2. Switch to **Streamlit → Price Alerts & Affiliate**:
   - Show the newly logged click row and its `sub_id`.
   - Open **Simulate Affiliate Conversion Webhook**, submit a confirmed booking commission, and watch **Conversions**, **Commission USD**, and **RPM** update live.
   - Create a Price Drop Alert for `CAI-DXB` (`2027-01-14`) at `<= USD 210` and click **Run Price Alert Check Now** to watch it evaluate against the live `$193.42` USD warehouse fare and increment `trigger_count`.

---

## Step 4 — Production ML Pipeline & Honest Cold-Start Guardrails (3 Minutes)

1. Switch to **Streamlit → ML Registry & Forecasts**.
2. Highlight the engineering separation between **live warehouse readiness** and **synthetic backtesting**:
   - **Live Warehouse Gate:** `cold_start_insufficient_history` (`observation_days: 1`, `0` promoted routes) — proves the system refuses to overfit or serve fake live ML predictions without longitudinal history.
   - **3-Year Synthetic Prototype (12 Cairo routes, 15,249 validation rows):** Leakage-free expanding-window backtest (`observed_date + 14d < validation_start`) achieving **ROC-AUC `0.8231`**, **Precision `85.2%`**, **Brier `0.0691` vs. `0.0837` baseline**, and **P50 MAE `$17.02` vs. `$26.25` baseline**.
3. Run the FastAPI ML decision endpoint live in terminal to show classifier + quantile band consistency:

   ```cmd
   curl "http://localhost:8000/api/v1/routes/CAI-DXB/ml-decision?departure_date=2027-01-14&allow_synthetic_prototype=true"
   ```

---

## Step 5 — Automated Quality & CI Verification (2 Minutes)

Run the linter and `pytest` suite covering retry/backoff logic, leakage-free backtesting, affiliate URL attribution, and FastAPI routes:

```cmd
docker compose exec api ruff check api dashboard ingestion ml tests
docker compose exec api pytest -v
```

Conclude by noting `.github/workflows/ci.yml` executes `ruff check`, `pytest`, and `dbt parse` automatically on every GitHub push.
