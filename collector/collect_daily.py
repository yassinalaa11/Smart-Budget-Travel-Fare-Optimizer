"""Daily SerpApi collector for SmartFare (runs OUTSIDE Databricks).

Budget: 5 flight searches + 2 hotel searches = 7 searches/day (~210/month),
under SerpApi's free plan of 250 successful searches/month.

Usage:
    python collector/collect_daily.py --dry-run   # writes CSVs to ./out, no upload
    python collector/collect_daily.py             # uploads CSVs to the Databricks Volume
    python collector/collect_daily.py --upload-test   # tests the Databricks upload only (0 searches)

Environment variables:
    SERPAPI_API_KEY      SerpApi key
    DATABRICKS_HOST      e.g. https://dbc-xxxx.cloud.databricks.com
    DATABRICKS_TOKEN     Databricks personal access token (only when uploading)
"""
from __future__ import annotations

import argparse
import datetime as dt
import io
import os
import sys
import time
from datetime import timezone
from pathlib import Path

import pandas as pd
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# ---- Search grid (edit here; keep total searches/day small) ----
FLIGHT_DESTS = ["DXB", "JED", "RUH", "DOH", "AMM"]
HOTEL_CITIES = {"DXB": "Dubai", "JED": "Jeddah"}
LEAD_DAYS = 30          # flight departure = today + 30 days
STAY_NIGHTS = 3         # hotel stay length, check-in = today + 30 days
ORIGIN = "CAI"

VOLUME_DIR = "/Volumes/workspace/sbto_bronze/raw_files/daily"


def make_session() -> requests.Session:
    s = requests.Session()
    s.mount("https://", HTTPAdapter(max_retries=Retry(
        total=4, backoff_factor=3, status_forcelist=[429, 500, 502, 503, 504])))
    return s


def collect_flights(s, api_key, collected_at, flight_date):
    rows = []
    for dest in FLIGHT_DESTS:
        try:
            data = s.get("https://serpapi.com/search", params={
                "engine": "google_flights", "departure_id": ORIGIN, "arrival_id": dest,
                "outbound_date": flight_date, "type": 2, "currency": "EGP",
                "gl": "eg", "hl": "en", "api_key": api_key,
            }, timeout=90).json()
        except (requests.exceptions.RequestException, ValueError) as e:
            print("skipped flights", dest, type(e).__name__)
            continue
        if "error" in data:
            print("api error flights", dest, data["error"])
            continue
        arrival = data.get("airports", [{}])[0].get("arrival", [{}])[0]
        for f in data.get("best_flights", []) + data.get("other_flights", []):
            seg = f["flights"]
            rows.append({
                "collected_at": collected_at,
                "origin": ORIGIN,
                "destination": dest,
                "destination_city": arrival.get("city"),
                "destination_country": arrival.get("country"),
                "flight_date": flight_date,
                "airline": ", ".join(dict.fromkeys(x["airline"] for x in seg)),
                "flight_number": ", ".join(x["flight_number"] for x in seg),
                "departure_time": seg[0]["departure_airport"]["time"],
                "arrival_time": seg[-1]["arrival_airport"]["time"],
                "duration_min": f.get("total_duration"),
                "stops": len(seg) - 1,
                "layovers": ", ".join(l["id"] for l in f.get("layovers", [])),
                "price_egp": f.get("price"),
                "airplane": seg[0].get("airplane"),
                "travel_class": seg[0].get("travel_class"),
                "co2_g": f.get("carbon_emissions", {}).get("this_flight"),
                "price_level": data.get("price_insights", {}).get("price_level"),
            })
        time.sleep(2)
    return pd.DataFrame(rows)


def collect_hotels(s, api_key, collected_at, check_in, check_out):
    rows = []
    for dest, city in HOTEL_CITIES.items():
        try:
            data = s.get("https://serpapi.com/search", params={
                "engine": "google_hotels", "q": f"hotels in {city}",
                "check_in_date": check_in, "check_out_date": check_out,
                "adults": 1, "currency": "EGP", "gl": "eg", "hl": "en",
                "api_key": api_key,
            }, timeout=90).json()
        except (requests.exceptions.RequestException, ValueError) as e:
            print("skipped hotels", dest, type(e).__name__)
            continue
        if "error" in data:
            print("api error hotels", dest, data["error"])
            continue
        for p in data.get("properties", []):
            rows.append({
                "collected_at": collected_at,
                "destination": dest,
                "city": city,
                "check_in": check_in,
                "check_out": check_out,
                "name": p.get("name"),
                "type": p.get("type"),
                "hotel_class": p.get("extracted_hotel_class"),
                "rating": p.get("overall_rating"),
                "reviews": p.get("reviews"),
                "price_per_night_egp": p.get("rate_per_night", {}).get("extracted_lowest"),
                "total_price_egp": p.get("total_rate", {}).get("extracted_lowest"),
                "latitude": p.get("gps_coordinates", {}).get("latitude"),
                "longitude": p.get("gps_coordinates", {}).get("longitude"),
                "free_cancellation": p.get("free_cancellation"),
                "amenities": ", ".join(p.get("amenities", [])),
            })
        time.sleep(2)
    return pd.DataFrame(rows)


def upload_to_volume(name: str, df: pd.DataFrame) -> None:
    """Upload one CSV with a plain REST call (Files API), no SDK needed."""
    host = os.environ["DATABRICKS_HOST"].strip().rstrip("/")
    token = os.environ["DATABRICKS_TOKEN"].strip()
    path = f"{VOLUME_DIR}/{name}"
    r = requests.put(
        f"{host}/api/2.0/fs/files{path}",
        params={"overwrite": "true"},
        headers={"Authorization": f"Bearer {token}",
                 "Content-Type": "application/octet-stream"},
        data=df.to_csv(index=False).encode("utf-8"),
        timeout=120,
    )
    if not r.ok:
        # Print the real reason (HTTP status + body) so failures are easy to diagnose.
        print("UPLOAD FAILED", r.status_code, r.text[:500])
        r.raise_for_status()
    print("uploaded", path, len(df), "rows")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="write CSVs to ./out instead of uploading")
    ap.add_argument("--upload-test", action="store_true",
                    help="upload a tiny test file to Databricks; uses NO SerpApi searches")
    args = ap.parse_args()

    if args.upload_test:
        upload_to_volume("upload_test.csv", pd.DataFrame({"ok": [1]}))
        return 0

    api_key = os.getenv("SERPAPI_API_KEY")
    if not api_key:
        print("Set SERPAPI_API_KEY"); return 1

    now = dt.datetime.now(timezone.utc)
    collected_at = now.isoformat(timespec="seconds")
    today = now.date()
    flight_date = (today + dt.timedelta(days=LEAD_DAYS)).isoformat()
    check_in = flight_date
    check_out = (today + dt.timedelta(days=LEAD_DAYS + STAY_NIGHTS)).isoformat()

    s = make_session()
    flights = collect_flights(s, api_key, collected_at, flight_date)
    hotels = collect_hotels(s, api_key, collected_at, check_in, check_out)
    print("flights:", flights.shape, "hotels:", hotels.shape)

    # Fail loudly if nothing came back, so the GitHub Action shows a red X.
    if flights.empty and hotels.empty:
        print("No data collected"); return 1

    stamp = today.isoformat()
    outputs = {}
    if not flights.empty:
        outputs[f"flights_{stamp}.csv"] = flights
    if not hotels.empty:
        outputs[f"hotels_{stamp}.csv"] = hotels

    if args.dry_run:
        out = Path("out"); out.mkdir(exist_ok=True)
        for name, df in outputs.items():
            df.to_csv(out / name, index=False)
            print("wrote", out / name)
    else:
        for name, df in outputs.items():
            upload_to_volume(name, df)
    return 0


if __name__ == "__main__":
    sys.exit(main())
