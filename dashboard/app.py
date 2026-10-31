import os

import httpx
import pandas as pd
import streamlit as st

API_URL = os.getenv("SMARTFARE_API_URL", "http://localhost:8000").rstrip("/")

st.set_page_config(page_title="SmartFare", page_icon="✈️", layout="wide")
st.title("SmartFare Price History")
st.caption("Observed fares by route and travel date")


@st.cache_data(ttl=60)
def get_json(path: str) -> list[dict] | dict:
    response = httpx.get(f"{API_URL}{path}", timeout=10.0)
    response.raise_for_status()
    return response.json()


try:
    routes = get_json("/api/v1/routes")
except httpx.HTTPError as exc:
    st.error(f"Could not load routes from the API: {exc}")
    st.stop()

if not routes:
    st.info("No price observations are available yet.")
    st.stop()

route_by_id = {route["route_id"]: route for route in routes}
selected_route = st.selectbox(
    "Route",
    options=list(route_by_id),
    format_func=lambda key: (
        f"{route_by_id[key]['origin_iata']} → {route_by_id[key]['destination_iata']}"
    ),
)

try:
    departure_options = get_json(f"/api/v1/routes/{selected_route}/departures")
except httpx.HTTPError as exc:
    st.error(f"Could not load travel dates from the API: {exc}")
    st.stop()

if not departure_options:
    st.info("No travel dates are available for this route yet.")
    st.stop()

option_labels = {
    f"Depart {item['departure_date']} · return {item['return_date']}": item
    for item in departure_options
}
selected_label = st.selectbox("Travel dates", options=list(option_labels))
selected_dates = option_labels[selected_label]
departure_date = selected_dates["departure_date"]
return_date = selected_dates["return_date"]

try:
    history = get_json(
        f"/api/v1/routes/{selected_route}/price-history"
        f"?departure_date={departure_date}&return_date={return_date}"
    )
except httpx.HTTPError as exc:
    st.error(f"Could not load price history from the API: {exc}")
    st.stop()

points = history["points"]
if any(point["contains_demo_data"] for point in points):
    st.warning("This route contains synthetic demo fares; do not use them for booking decisions.")
elif os.getenv("SMARTFARE_PROVIDER", "mock").strip().lower() == "travelpayouts":
    st.info(
        "Prices come from Travelpayouts' cached calendar data. They are useful for trends, "
        "but are not guaranteed live booking quotes."
    )

if points:
    latest = points[-1]
    left, right, meta = st.columns(3)
    left.metric("Latest observed minimum", f"{latest['currency_code']} {latest['min_total_price']}")
    right.metric("Latest observed median", f"{latest['currency_code']} {latest['median_total_price']}")
    meta.metric("Observation days", len({point["observed_date"] for point in points}))

    chart_data = pd.DataFrame(points)
    chart_data["observed_date"] = pd.to_datetime(chart_data["observed_date"])
    chart_data = chart_data.set_index("observed_date")[["min_total_price", "median_total_price"]]
    st.line_chart(chart_data)
    st.dataframe(pd.DataFrame(points), use_container_width=True, hide_index=True)
