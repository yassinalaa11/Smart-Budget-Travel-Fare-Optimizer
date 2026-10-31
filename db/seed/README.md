# Seed dimensions

- `dim_routes.csv` contains the initial 15 Cairo-origin route pairs. Distance and
  typical duration are intentionally blank until an authoritative route source is
  selected.
- `dim_hotels.csv` contains 3-, 4-, and 5-star market tiers for each destination.
  These rows define market categories; they are not a list of specific properties.
- Run `python db/seed/build_calendar.py` to generate `dim_calendar.csv` for 2026–2027.
  Weekday uses ISO numbering (Monday = 1, Sunday = 7). Holiday and school-break
  fields are blank because those vary by country and have not yet been sourced.

The CSVs are seed inputs; they do not load themselves into PostgreSQL.

After applying `db/migrations/002_dimensions.sql`, run
`docker compose exec api python db/seed/load_dimensions.py` to upsert the CSVs.
The loader is safe to rerun and requires the API container to be rebuilt after its
Dockerfile starts copying the `db/` directory.
