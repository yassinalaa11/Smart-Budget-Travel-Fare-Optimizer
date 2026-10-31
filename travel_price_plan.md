# Travel Price Intelligence & Booking Recommendation Platform: Detailed Engineering & Product Plan

## 1. Product Vision: Shifting Travel Search from Discovery to Decision

Standard travel metasearch platforms show what a flight or hotel costs at the exact moment of a query, leaving budget-conscious travelers to guess whether fares will drop tomorrow or spike before they book. This platform shifts the user experience from manual price comparison to automated decision support by continuously collecting daily price snapshots across tracked routes and hotel markets, constructing an independent longitudinal price database, and answering two concrete questions:

1. **Timing Decision ("Book Now vs. Wait"):** Given the current quoted price, the number of days until departure (`days_to_departure`), and historical price trajectories for this route and season, what is the probability that the price will drop by a meaningful margin before departure versus rising?
2. **Calendar Optimization ("Cheapest Travel Windows"):** Across a flexible date range or season, which specific departure-return combinations and length-of-stay windows offer the lowest expected total trip cost (flight + hotel combined)?

| Dimension | Traditional Metasearch (Google Flights / Skyscanner) | Travel Price Intelligence Platform (Our Solution) |
| :--- | :--- | :--- |
| **Primary User Intent** | "Show me all flights from Cairo to London on November 15." | "Tell me the cheapest week to visit London this winter and whether to lock in today's fare." |
| **Data Retention** | Ephemeral search caching; limited consumer visibility into raw historical booking curves. | Persistent daily time-series snapshots across fixed lead-time horizons (1 to 180 days out). |
| **Core Output** | Ranked list of current itineraries and booking links. | Actionable **Buy / Wait / Hold** signal with expected savings, confidence intervals, and lowest-cost date matrices. |
| **Combined Trip Economics** | Flights and hotels are searched in isolated tabs. | Joint optimization of flight fares and nightly hotel rates for the same travel window. |

## 2. Ingestion Architecture: Scheduled Multi-Horizon API Collection

Because travel prices are perishable—once a departure date passes, its historical lead-time curve cannot be reconstructed retroactively from free public endpoints—**data collection must begin on Day 1** on a strict schedule.

### Tracked Route & Market Matrix
To stay within API rate limits and budget constraints during Phase 1, ingestion focuses on a curated set of high-volume origin-destination (O&D) pairs and corresponding destination hotel markets:
* **Initial Scope:** 10 to 25 high-demand routes (e.g., regional hubs to major leisure/business destinations) and top 3 hotel star-rating tiers (3-star, 4-star, budget boutique) per destination city.
* **Lead-Time Horizon Grid (`days_to_departure`):** Rather than querying every single future calendar day (which would require 365 requests per route per day), the collector samples a structured lead-time grid for each route every day:
  * **High-Frequency Near Horizon (1 to 30 days out):** Daily departure dates (+1, +2, +3, ..., +30 days) where airline revenue management algorithms adjust fare buckets most aggressively.
  * **Medium Horizon (31 to 90 days out):** Sampled every 3 days (+33, +36, ..., +90 days) plus all Friday/Sunday weekend departure pairs.
  * **Far Horizon (91 to 180 days out):** Sampled weekly (+97, +104, ..., +180 days) to establish baseline early-bird fare floors.
* **Length-of-Stay (LoS) Templates:** Standard 3-night (weekend), 7-night (1-week), and 14-night return windows for round-trip flights and matching hotel check-in/check-out windows.

### Travel API Sources & Orchestration
* **Flight & Hotel Pricing APIs:** Connect to [Amadeus Flight Offers Search API](https://www.postman.com/amadeus4dev/amadeus-for-developers-s-public-workspace/request/nvshvdh/flight-offers-search) for granular fare bucket and baggage metadata, supplemented by [Travelpayouts Data & Partner APIs](https://support.travelpayouts.com/hc/en-us/articles/212246627-For-developers-and-travel-startups) for cached cheapest-fare discovery and hotel rate feeds.
* **Workflow Orchestration:** Managed via [Prefect](https://www.prefect.io/compare/airflow) (recommended for rapid Python-native setup and lower container memory overhead) or Apache Airflow([Fastero Comparison](https://fastero.com/blog/airflow-vs-prefect-data-orchestration-compared)).
  * **Retry & Backoff Logic:** Exponential backoff with jitter (`2^attempt` seconds) on HTTP `429 Too Many Requests` and `5xx` gateway timeouts.
  * **Token Bucket Rate Limiter:** Enforces per-second and daily quota caps per API provider so jobs never trigger account suspension or unexpected overage billing.

### Request Audit Logging (`ingestion_request_log`)
Every outbound API call is logged before and after execution to ensure full observability and cost tracking:

| Column Name | Data Type | Description |
| :--- | :--- | :--- |
| `request_id` | `UUID` (PK) | Unique identifier generated per outbound HTTP call. |
| `dag_run_id` | `VARCHAR(128)` | Orchestrator run ID linking the request to a scheduled batch. |
| `provider_name` | `VARCHAR(64)` | API source (`amadeus_flights`, `travelpayouts_hotels`, etc.). |
| `endpoint_url` | `TEXT` | Normalized endpoint path requested. |
| `query_params` | `JSONB` | Origin, destination, departure date, return date, adults, cabin class. |
| `http_status` | `INT` | Returned status code (`200`, `429`, `500`, `503`). |
| `latency_ms` | `INT` | Round-trip response duration in milliseconds. |
| `records_returned` | `INT` | Count of flight itineraries or hotel property offers in the payload. |
| `requested_at_utc` | `TIMESTAMPTZ` | Exact UTC timestamp when the request was dispatched. |

## 3. Two-Tier Storage Architecture preserve Raw Fidelity and Query Speed

Storing both raw API payloads and normalized relational tables in PostgreSQL provides a single, low-maintenance database engine for MVP while supporting full replayability if parsing logic evolves.

### Layer 1: Raw Landing Zone (`raw` Schema)
* **Purpose:** Immutable append-only audit log of exact API responses. If an API adds a new baggage fee field or changes its JSON hierarchy, downstream tables can be backfilled without re-querying external APIs.
* **Table Structure (`raw.api_responses`):**
  * `response_id` (`UUID`, PK)
  * `request_id` (`UUID`, FK to `ingestion_request_log`)
  * `provider_name` (`VARCHAR(64)`)
  * `ingested_date` (`DATE`, Partition Key — partitioned monthly to allow fast archiving to cold object storage after 6–12 months)
  * `raw_payload` (`JSONB` — compressed automatically by PostgreSQL TOAST)
  * `payload_hash` (`CHAR(64)` — SHA-256 hash of canonical JSON to detect identical consecutive responses)

### Layer 2: Clean Structured Analytical Layer (`analytics` Schema)
Transformed via [dbt Core](https://docs.getdbt.com/docs/introduction) from `raw.api_responses` into a star schema optimized for time-series window functions and dashboard queries:

* **Dimension Tables:**
  * `dim_routes`: `route_id`, `origin_iata`, `destination_iata`, `origin_city`, `destination_city`, `distance_km`, `is_international`, `typical_flight_duration_min`.
  * `dim_hotels`: `hotel_id`, `city_code`, `property_name`, `star_rating`, `latitude`, `longitude`, `neighborhood`, `amenity_tier`.
  * `dim_calendar`: `date_key`, `day_of_week`, `is_weekend`, `month_num`, `season_name`, `is_origin_public_holiday`, `is_destination_public_holiday`, `school_break_flag`.
* **Fact Tables:**
  * `fct_flight_price_observations`:
    * **Grain:** One row per `(route_id, departure_date, return_date, observed_date, cabin_class)`.
    * **Metrics:** `min_total_price_usd`, `median_top5_price_usd`, `cheapest_direct_price_usd`, `cheapest_1stop_price_usd`, `min_duration_minutes`, `carrier_count`, `Includes_checked_bag_flag`.
  * `fct_hotel_price_observations`:
    * **Grain:** One row per `(city_code, star_rating_tier, checkin_date, checkout_date, observed_date)`.
    * **Metrics:** `min_nightly_rate_usd`, `p25_nightly_rate_usd`, `median_nightly_rate_usd`, `available_properties_count`, `free_cancellation_rate_usd`.

## 4. Automated Data Quality Gateways Stop Corrupt Observations Before Modeling

Travel APIs frequently return transient anomalies—such as currency conversion glitches, phantom sold-out fares, or duplicate itinerary codeshares—that can distort rolling averages and ML training. Quality checks run automatically via `dbt test` and custom Python validators between the `raw` and `analytics` layers:

| Quality Dimension | Specific Validation Rule | Automated Remediation Action |
| :--- | :--- | :--- |
| **Deduplication** | Enforce uniqueness on `(route_id, departure_date, return_date, observed_date)` for aggregated facts; filter codeshare duplicates in staging where flight numbers differ but operating carrier, departure timestamp, and price are identical. | Keep the lowest total fare observation per unique operating itinerary; log dropped duplicates count. |
| **Missingness & Completeness** | Flag any scheduled `dag_run_id` where fewer than 95% of tracked routes returned at least 1 valid offer, or where `min_total_price_usd IS NULL`. | Trigger Prefect retry task for failed routes; if an API outage persists for a day, impute using linear interpolation bounded by adjacent lead-time days and set `is_imputed = TRUE`. |
| **Currency Normalization** | Verify `currency_code = 'USD'` (or base platform currency). Occasionally an API defaults to local origin currency despite request headers. | Cross-check against daily exchange rates table; reject or convert any payload where raw currency differs from requested currency. |
| **Outlier & Spike Filtering** | Flag observations where `min_total_price_usd < $15` (tax-only parsing error) or where price jumps `> 400%` in 24 hours while ` median_top5_price_usd` remains stable (single-seat fare bucket glitch). | Quarantine extreme outliers into `analytics.quarantined_observations` and use `p10` / `median_top5_price_usd` as the robust price signal so single-ticket errors never trigger false "Book Now" alerts. |

## 5. Time-Series Feature Engineering Captures Airline Revenue Management Patterns

Using [dbt Core](https://docs.getdbt.com/docs/introduction) SQL window functions and `pandas` feature pipelines, daily observations are enriched into a model-ready feature table (`ml_features_flight_pricing`).

### Core Feature Groups
1. **Lead-Time & Booking Curve Features:**
   * `days_to_departure`: Integer (`departure_date - observed_date`), the single strongest driver of airline pricing tiers (e.g., 21-day, 14-day, and 7-day advance purchase cliff boundaries).
   * `lead_time_bucket`: Categorical (`0-7d`, `8-14d`, `15-21d`, `22-45d`, `46-90d`, `90d+`) to capture step-function fare jumps when advance-purchase discount classes close.
2. **Rolling Price Momentum & Volatility Features:**
   * `rolling_mean_7d`, `rolling_mean_14d`, `rolling_std_7d`: Computed along the trajectory of the *same* `(route_id, departure_date)` over the past 7 and 14 observation days.
   * `price_velocity_3d` & `price_velocity_7d`: Percentage change in `min_total_price_usd` over the last 3 and 7 days (`(price_today - price_7d_ago) / price_7d_ago`).
   * `z_score_vs_route_horizon`: How many standard deviations today's fare sits above or below the historical average for that exact `route_id`, `month`, and `days_to_departure` bracket.
   * `drawdown_from_peak` & `markup_over_historical_min`: Ratio of current price to the lowest and highest prices observed so far for that departure date.
3. **Calendar, Seasonality & Event Features:**
   * Cyclical encodings (`sin`/`cos` transformations) of `day_of_year` and `day_of_week` for both departure and return dates.
   * `is_friday_sunday_pair`: Binary indicator for high-demand weekend leisure travel.
   * `days_to_nearest_holiday`: Distance in days from departure/return to major national/religious holidays at either origin or destination.
4. **Market Competition & Supply Proxies:**
   * `direct_vs_1stop_spread_pct`: Price premium of the cheapest nonstop flight over the cheapest 1-stop flight (when nonstop inventory tightens, this spread widens before overall fares jump).
   * `active_carrier_count`: Number of distinct airlines offering fares within 15% of the minimum price on that date.

## 6. Predictive Modeling & Cold-Start Roadmap

A common pitfall in travel ML projects is training complex gradient-boosted trees on 2 weeks of data, resulting in severe overfitting. Our modeling architecture uses a **staged maturity roadmap** that delivers immediate user value on Day 1 while predictive accuracy scales with data volume.

### Target Formulation
Rather than predicting an exact dollar price 14 days from now (which suffers from high variance), the model solves two well-calibrated decision tasks over a forward window $H$ (e.g., remaining days until departure, capped at next 14 days):
1. **Classification Target (`y_will_drop`):** Binary label $= 1$ if $\min_{t+1 \dots t+H}(\text{Price}) \le 0.93 \times \text{Price}_t$ (i.e., will the fare drop by at least 7% within the next 14 days?), else $0$.
2. **Quantile Regression Target (`y_min_future_price`):** Predict the 10th, 50th (median), and 90th percentiles of the minimum fare over the next $H$ days using pinball loss in [LightGBM](https://skforecast.org/0.15.1/user_guides/forecasting-xgboost-lightgbm.html), providing users with an explicit downside/upside dollar range (e.g., *"Expected to stay between $240 and $285; 82% chance prices rise from here"*).

### Staged Modeling Maturity

| Stage & Data Volume | Active Model Architecture | Decision Logic & User Transparency |
| :--- | :--- | :--- |
| **Phase 1: Cold Start (Weeks 1–4)** | **Cross-Horizon Statistical Baseline:** Rolling route-level quantiles across adjacent departure dates on the same route + heuristic advance-purchase cliff rules (21d/14d/7d). | Badge shown in UI: *"Historical Baseline Mode (collecting route history)"*. Recommends **Book Now** when current price is in the bottom 20th percentile of observed fares for that route's seasonal window. |
| **Phase 2: Early Longitudinal (Months 2–3)** | **Global Pooled [LightGBM](https://unit8co.github.io/darts/generated_api/darts.models.forecasting.lgbm.html) / XGBoost Classifier & Quantile Regressor:** Trained across all routes simultaneously using normalized price ratios (`price / route_mean_price`) and `days_to_departure`. | Evaluates against Phase 1 rolling-average baseline via walk-forward time-series cross-validation. Promoted to production only for routes where out-of-sample Precision/Brier score beats the baseline by $\ge 5\%$. |
| **Phase 3: Full Seasonality (Months 4–12+)** | **Hybrid Architecture ([Prophet](https://facebook.github.io/prophet/docs/quick_start.html) + LightGBM Ensemble):** Prophet models macro route-level seasonal and holiday curves; LightGBM ingests Prophet's residuals plus short-term momentum features (`price_velocity_3d`, carrier spread). | Full **Book Now vs. Wait** expected-value engine with calibrated probability scores and historical accuracy scorecards displayed per route. |

### Backtesting Protocol (Preventing Data Leakage)
* **Strict Expanding-Window Split:** Never use random $k$-fold cross-validation on time-series data. Training sets must only include observations where `observed_date + H < validation_observed_date` so future price movements never leak into training features.
* **Business Metric Evaluation:** Beyond ROC-AUC and RMSE, models are evaluated on **Simulated Traveler Regret ($)**—the average dollar difference between following the model's recommendation (`Book Now` vs. `Wait`) and the theoretical hindsight-optimal decision.

## 7. Serving Layer: FastAPI Backend & Streamlit Decision Dashboard

The serving stack decouples inference/query logic from the presentation layer using [FastAPI](https://fastapi.tiangolo.com/tutorial/) and [Streamlit](https://docs.streamlit.io/get-started/fundamentals/main-concepts), containerized via Docker Compose.

### FastAPI Service Endpoints
* `GET /api/v1/routes/{route_id}/recommendation?departure_date=YYYY-MM-DD&return_date=YYYY-MM-DD`
  * Returns current cheapest fare, historical percentile rank, model recommendation (`BOOK_NOW`, `WAIT`, or `NEUTRAL`), confidence probability, expected price range over the next 7–14 days, and primary explanation drivers (e.g., *"Fare is 18% below the 30-day route average and approaching the 14-day advance-purchase cliff"*).
* `GET /api/v1/routes/{route_id}/cheapest-windows?month=YYYY-MM&stay_nights=7`
  * Returns a ranked matrix of departure dates in that month combining flight fare + median nightly hotel cost (`total_estimated_trip_cost`), highlighting the top 3 lowest-cost travel windows.
* `GET /api/v1/routes/{route_id}/price-history?departure_date=YYYY-MM-DD`
  * Returns the daily time series of observed fares for that specific departure date alongside the historical 25th–75th percentile band for that route.
* `GET /api/v1/redirect/book`
  * Logs the click event (`route_id`, `recommendation_shown`, `partner_id`, `timestamp`), constructs the deep-linked affiliate URL, and issues an HTTP `302` redirect to the airline/OTA checkout page.

### Streamlit User Interface Views
1. **The Decision Card ("Should I Book Today?"):** Color-coded status banner (**Green: Book Now — Good Deal**, **Amber: Fair Price — Lock In If Inflexible**, **Blue: Wait — High Chance of Price Drop**) with a plain-English explanation and a confidence meter showing how many days of historical data back the recommendation.
2. **Historical Booking Curve Chart:** Interactive Plotly chart showing how the selected flight's price has moved over time compared to the typical "cone" of prices as `days_to_departure` counts down from 90 days to 0.
3. **Total Trip Cost Heatmap (Flight + Hotel):** A calendar matrix where each cell shows the combined price of the round-trip flight plus a 3-star or 4-star hotel for $N$ nights, preventing travelers from booking a cheap Tuesday flight only to get hit by a $400/night conference hotel surge at the destination.

## 8. Affiliate Monetization & Attribution Pipeline

Affiliate marketing keeps the platform 100% free for travelers while aligning incentives: users return when our "Wait" alerts save them money, and we earn commissions when our "Book Now" signal converts into a purchase.

### Affiliate Integration Mechanics
* **Aggregator Networks:** Integrate [Travelpayouts Partner Links API](https://support.travelpayouts.com/hc/en-us/articles/25289759198226-API-for-Travelpayouts-partner-links) and Skyscanner/Kayak/https://www.google.com/search?q=Booking.com affiliate programs via a unified link builder service.
* **Pre-Filled Deep Linking:** Rather than dropping users on a generic airline homepage, the `/api/v1/redirect/book` endpoint constructs deep links pre-populated with `origin`, `destination`, `departure_date`, `return_date`, and `passengers` so the user lands directly on the checkout or itinerary confirmation screen, maximizing conversion rate.
* **Sub-ID Tracking (`sub_id`):** Append a unique `click_id` parameter to every affiliate redirect URL. When a booking webhook or daily commission report arrives from the affiliate network, join `click_id` back to our internal `click_events` table in PostgreSQL to measure which routes and which recommendation signals (`BOOK_NOW` vs. `Cheapest Calendar Window`) generate the highest revenue per thousand impressions (RPM).
* **Price Alert Email/Telegram Loop:** Users who receive a **"Wait"** recommendation shouldn't just bounce—offer a 1-click *"Alert me when this route drops below $X"* signup. When the nightly pipeline detects the target price drop, it automatically dispatches a notification containing the affiliate booking link, capturing high-intent conversions.

## 9. Phased Implementation Roadmap

| Phase | Timeline | Engineering Milestones | Key Deliverable |
| :--- | :--- | :--- | :--- |
| **Phase 1: Ingestion & Storage Foundation** | Weeks 1–2 | Dockerize PostgreSQL + Prefect; implement API collectors for 15 core routes & hotel markets; build `raw.api_responses` and `ingestion_request_log`. | Automated daily collection running unattended with Slack/Telegram failure alerts. |
| **Phase 2: Quality & Analytics Marts** | Weeks 3–4 | Set up [dbt Core](https://docs.getdbt.com/docs/introduction) models (`dim_routes`, `fct_flight_price_observations`); implement deduplication, currency checks, and outlier quarantine rules. | Clean analytical tables populated daily + Phase 1 statistical baseline percentiles. |
| **Phase 3: MVP Serving & Affiliate Links** | Weeks 5–6 | Build [FastAPI](https://fastapi.tiangolo.com/tutorial/) endpoints and [Streamlit](https://docs.streamlit.io/get-started/fundamentals/main-concepts) UI powered by Phase 1 statistical quantiles; integrate Travelpayouts deep links and click tracking. | Live public MVP showing cheapest travel windows, historical price charts, and baseline deal ratings. |
| **Phase 4: ML Pipeline & Backtesting** | Weeks 7–10 | Build feature pipeline (`days_to_departure`, rolling velocity, holiday flags); train [LightGBM](https://skforecast.org/0.15.1/user_guides/forecasting-xgboost-lightgbm.html) quantile/classification models; run walk-forward backtests vs. rolling baseline. | ML-driven **Book Now vs. Wait** predictions deployed for routes passing backtest thresholds. |

## 10. Stakeholder FAQ & Pitch Defense Playbook

When presenting this project to technical reviewers, investors, or users, use these structured answers:

### Q1: "How is this different from Google Flights or Skyscanner?"
* **Decision-First vs. Search-First:** Google Flights and Skyscanner are built primarily as *search engines* for travelers who already know their dates or want to browse current inventory. Our platform is built as a *timing and budget optimizer*—it answers **"Which dates give me the lowest combined flight + hotel cost?"** and **"Should I buy today or wait 5 days?"**
* **Combined Flight + Hotel Economics:** A $90 flight is a bad deal if a major convention makes hotels at the destination cost $350/night that week. By ingesting both flight and hotel price histories for the same travel windows, we rank dates by *true total trip cost*.
* **Transparent Proprietary History & Model Backtesting:** Instead of an opaque "prices are typical" badge, we show our own collected booking curve for that route, the explicit probability of a price drop, and how our model performs against a transparent rolling-average baseline.

### Q2: "How can your ML model predict prices accurately when you're just starting out?"
* **Upfront Data Honesty:** We are completely transparent that machine learning models require longitudinal data across multiple lead-time horizons before they can outperform statistical rules. That is why **automated daily ingestion is the very first component we deploy**.
* **Graceful Degradation (Cold-Start Design):** During the first 4–6 weeks of collection, the platform does not pretend to run black-box ML. Instead, it uses a deterministic **Phase 1 Statistical Baseline** (comparing today's price against cross-horizon rolling quantiles for that route and well-established airline advance-purchase cliffs at 21, 14, and 7 days prior to departure).
* **Earned Model Promotion:** Each route displays a "Data Maturity" indicator. A route only graduates from the statistical baseline to the LightGBM predictive model once our automated walk-forward backtest proves the ML model beats the rolling baseline out-of-sample.

### Q3: "Won't API costs become too expensive if you check prices every day?"
* **Smart Horizon Sampling:** We don't brute-force query every calendar day 365 days out. We use a dense sampling grid for the next 30 days (where prices move daily) and a sparser grid for 31–180 days out, reducing API calls by ~70% while still capturing the full shape of the booking curve.
* **Batch Caching + Affiliate Tiers:** By combining free/affiliate cached endpoints (such as [Travelpayouts Data APIs](https://support.travelpayouts.com/hc/en-us/articles/212246627-For-developers-and-travel-startups)) for broad calendar discovery with targeted live calls ([Amadeus Flight Offers Search](https://www.postman.com/amadeus4dev/amadeus-for-developers-s-public-workspace/request/nvshvdh/flight-offers-search)) on high-interest dates, ingestion costs remain near zero at MVP scale.
