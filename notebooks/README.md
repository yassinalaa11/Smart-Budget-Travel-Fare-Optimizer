# Notebooks

## SerpApi live snapshots

`serpapi_live_snapshots.ipynb` contains the flight and hotel collection prototype. It queries upcoming travel dates and saves CSV files under `data/`. It is useful for demonstrating ingestion and storing current snapshots.

It does **not** provide prices from past years and is not the historical dataset required to recommend the cheapest season based on past-year prices.

Before running it, set `SERPAPI_API_KEY` in the shell environment. Do not put API keys in notebooks, CSV files, or Git. The notebook has been saved without cell outputs.
