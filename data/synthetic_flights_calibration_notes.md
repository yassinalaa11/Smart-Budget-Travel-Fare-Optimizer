# Flight dataset calibration notes

This file is a synthetic training/demo dataset calibrated to a small Travelpayouts sample. It is not real fare history. Every row remains marked `is_synthetic=True`, and the `source` column identifies the calibration.

## Calibration reference

Reference summary supplied from `analytics_marts.fct_flight_price_observations` on 2026-11-15:

| Route | Real observations | Observation dates | Min USD | Median USD | Max USD |
|---|---:|---:|---:|---:|---:|
| CAI-DXB | 15 | 2 | 194 | 278 | 290 |
| CAI-IST | 15 | 2 | 153 | 230 | 268 |

Only those two routes were retained because they had the strongest real sample coverage and existed in the original synthetic file. Other routes had 1-4 observations and were excluded from this calibration.

## Changes made

- Kept the original flight columns and row-level synthetic labels.
- Rescaled each route's synthetic prices piecewise so its minimum, median, and maximum match the reference summary. Values between those anchors retain the synthetic rows' relative ordering and variation.
- Shifted both observation and departure dates forward 19 days, preserving `days_to_departure`, so the 60-day observation window ends on 2026-11-15.
- Kept currency as USD.

This calibration is for pipeline and model prototyping only. The reference has just 15 observations per route across two observation dates, which is not enough to validate real-world forecasting accuracy. Hotel files were not calibrated because SmartFare has no real hotel-price observations.