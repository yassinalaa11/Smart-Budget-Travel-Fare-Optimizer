import os
from datetime import date

import httpx
import pandas as pd
import streamlit as st

from modeling import (
    flight_curve,
    load_flights,
    load_hotels,
    hotel_curve,
    train_flight_model,
    train_hotel_model,
)

API_URL = os.getenv("SMARTFARE_API_URL", "http://localhost:8000").rstrip("/")

st.set_page_config(page_title="SmartFare", page_icon="✈️", layout="wide")
st.title("SmartFare")
st.caption("A travel-price analytics prototype: observed sample data plus clearly labeled synthetic prediction demos.")


@st.cache_data(ttl=60)
def get_json(path: str) -> list[dict] | dict:
    response = httpx.get(f"{API_URL}{path}", timeout=10.0)
    response.raise_for_status()
    return response.json()


@st.cache_resource
def flight_assets():
    frame = load_flights()
    model, metrics = train_flight_model(frame)
    return frame, model, metrics


@st.cache_resource
def hotel_assets():
    frame, hotels = load_hotels()
    model, metrics = train_hotel_model(frame)
    return frame, hotels, model, metrics


observed_tab, prediction_tab = st.tabs(["Observed flight prices", "Prediction demo"])

with observed_tab:
    st.subheader("Collected flight-price history")
    st.caption("This is the current provider sample. A small sample is not sufficient for trustworthy forecasts.")
    try:
        routes = get_json("/api/v1/routes")
        if not routes:
            st.info("No price observations are available yet.")
        else:
            route_by_id = {route["route_id"]: route for route in routes}
            selected_route = st.selectbox(
                "Route", options=list(route_by_id), key="observed_route",
                format_func=lambda key: f"{route_by_id[key]['origin_iata']} → {route_by_id[key]['destination_iata']}",
            )
            departures = get_json(f"/api/v1/routes/{selected_route}/departures")
            if not departures:
                st.info("No travel dates are available for this route yet.")
            else:
                labels = {
                    f"Depart {item['departure_date']} · return {item['return_date']}": item
                    for item in departures
                }
                choice = st.selectbox("Travel dates", list(labels), key="observed_dates")
                dates = labels[choice]
                history = get_json(
                    f"/api/v1/routes/{selected_route}/price-history"
                    f"?departure_date={dates['departure_date']}&return_date={dates['return_date']}"
                )
                points = history["points"]
                if any(point["contains_demo_data"] for point in points):
                    st.warning("This route includes mock/demo observations.")
                elif os.getenv("SMARTFARE_PROVIDER", "mock").strip().lower() == "travelpayouts":
                    st.info("Travelpayouts cached calendar observations; these are not guaranteed live booking quotes.")
                if points:
                    latest = points[-1]
                    a, b, c = st.columns(3)
                    a.metric("Latest observed minimum", f"{latest['currency_code']} {latest['min_total_price']}")
                    b.metric("Latest observed median", f"{latest['currency_code']} {latest['median_total_price']}")
                    c.metric("Observation days", len({p["observed_date"] for p in points}))
                    chart = pd.DataFrame(points)
                    chart["observed_date"] = pd.to_datetime(chart["observed_date"])
                    st.line_chart(chart.set_index("observed_date")[["min_total_price", "median_total_price"]])
                    st.dataframe(pd.DataFrame(points), use_container_width=True, hide_index=True)
    except (httpx.HTTPError, KeyError, ValueError) as exc:
        st.error(f"Could not load the observed sample from the API: {exc}")

with prediction_tab:
    st.subheader("When might prices be lower?")
    st.warning(
        "Classroom prototype only. Predictions are trained on synthetic data, including flight prices "
        "calibrated to a very small real sample. They are not booking advice or validated real-world forecasts."
    )
    mode = st.radio("Demo dataset", ["Flights", "Hotels"], horizontal=True)
    try:
        if mode == "Flights":
            flights, flight_model, metrics = flight_assets()
            routes = sorted(flights["route_id"].unique())
            route = st.selectbox("Route", routes, key="demo_flight_route")
            depart_month = st.selectbox("Departure month", list(range(1, 13)), index=0)
            weekday = st.selectbox("Departure weekday (Monday = 0)", list(range(7)), index=4)
            curve = flight_curve(flights, flight_model, route, depart_month, weekday)
            currency = flights.loc[flights.route_id == route, "currency"].mode().iloc[0]
            best = curve.loc[curve.predicted_price.idxmin()]
            x, y, z = st.columns(3)
            x.metric("Lowest model estimate", f"{currency} {best.predicted_price:,.0f}", f"about {int(best.days_to_departure)} days before")
            y.metric("Holdout MAE", f"{currency} {metrics['mae']:,.1f}")
            z.metric("Simple baseline MAE", f"{currency} {metrics['baseline_mae']:,.1f}")
            st.caption(f"Chronological holdout: {metrics['train_rows']:,} training rows, {metrics['test_rows']:,} later rows. Lower MAE is better.")
            st.line_chart(curve.set_index("days_to_departure")[["predicted_price"]])
            st.dataframe(curve, use_container_width=True, hide_index=True)
        else:
            hotels_frame, hotels, hotel_model, metrics = hotel_assets()
            hotel_map = hotels.set_index(hotels.hotel_id.astype(str))
            hotel_id = st.selectbox("Hotel", list(hotel_map.index), format_func=lambda key: f"{hotel_map.loc[key, 'name']} · {hotel_map.loc[key, 'city']}")
            hotel = hotel_map.loc[hotel_id]
            arrival = st.date_input("Check-in date", value=date(2027, 1, 15), min_value=date.today())
            nights = st.number_input("Nights", min_value=1, max_value=30, value=3)
            adults = st.number_input("Adults", min_value=1, max_value=8, value=2)
            curve = hotel_curve(hotels_frame, hotel_model, hotel, arrival.month, arrival.weekday(), int(nights), int(adults))
            best = curve.loc[curve.predicted_nightly.idxmin()]
            x, y, z = st.columns(3)
            x.metric("Lowest estimated nightly price", f"USD {best.predicted_nightly:,.0f}", f"about {int(best.days_to_checkin)} days before")
            y.metric("Estimated stay total", f"USD {best.predicted_stay_total:,.0f}")
            z.metric("Holdout MAE / night", f"USD {metrics['mae']:,.1f}")
            st.caption(f"Chronological holdout: {metrics['train_rows']:,} training rows, {metrics['test_rows']:,} later rows. Baseline MAE: USD {metrics['baseline_mae']:,.1f} per night.")
            st.line_chart(curve.set_index("days_to_checkin")[["predicted_nightly"]])
            st.dataframe(curve, use_container_width=True, hide_index=True)
    except (FileNotFoundError, ValueError, KeyError) as exc:
        st.error(f"Could not prepare the prediction demo: {exc}")
