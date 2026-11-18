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


observed_tab, serpapi_tab, hotel_tab, prediction_tab = st.tabs(
    ["Observed flight prices", "SerpApi flights", "SerpApi hotels", "Prediction demo"]
)


@st.cache_data(ttl=60)
def load_serpapi_snapshot() -> pd.DataFrame:
    frame = pd.DataFrame(get_json("/api/v1/serpapi/flights?limit=2000"))
    for column in ["min_total_price", "median_total_price", "offer_count", "response_count"]:
        if column in frame:
            frame[column] = pd.to_numeric(frame[column], errors="coerce")
    return frame


@st.cache_data(ttl=60)
def load_serpapi_hotels() -> pd.DataFrame:
    frame = pd.DataFrame(get_json("/api/v1/serpapi/hotels?limit=2000"))
    for column in [
        "nightly_price", "total_price_egp", "median_total_price", "observation_count",
        "hotel_class", "rating", "review_count",
    ]:
        if column in frame:
            frame[column] = pd.to_numeric(frame[column], errors="coerce")
    return frame

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
                    (
                        f"Depart {item['departure_date']} · "
                        + (f"return {item['return_date']}" if item["return_date"] else "one-way")
                    ): item
                    for item in departures
                }
                choice = st.selectbox("Travel dates", list(labels), key="observed_dates")
                dates = labels[choice]
                history_path = (
                    f"/api/v1/routes/{selected_route}/price-history"
                    f"?departure_date={dates['departure_date']}"
                )
                if dates["return_date"]:
                    history_path += f"&return_date={dates['return_date']}"
                history = get_json(history_path)
                points = history["points"]
                providers = sorted({point["provider"] for point in points})
                if providers:
                    provider = st.selectbox("Data provider", providers, key="observed_provider")
                    points = [point for point in points if point["provider"] == provider]
                currencies = sorted({point["currency_code"] for point in points})
                if currencies:
                    currency = st.selectbox("Currency", currencies, key="observed_currency")
                    points = [point for point in points if point["currency_code"] == currency]
                cabins = sorted({point["cabin_class"] for point in points if point.get("cabin_class")})
                if cabins:
                    cabin = st.selectbox("Cabin class", cabins, key="observed_cabin")
                    points = [point for point in points if point.get("cabin_class") == cabin]
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
                    for column in ["min_total_price", "median_total_price"]:
                        chart[column] = pd.to_numeric(chart[column], errors="coerce")
                    observation_days = chart["observed_date"].nunique()
                    if observation_days > 1:
                        st.line_chart(chart.set_index("observed_date")[["min_total_price", "median_total_price"]])
                    else:
                        st.bar_chart(pd.DataFrame(
                            {"price": [
                                float(latest["min_total_price"]),
                                float(latest["median_total_price"]),
                            ]},
                            index=["Minimum", "Median"],
                        ))
                        st.info("Only one observation day is available. This is a price summary for that day, not a trend over time.")
                    st.dataframe(pd.DataFrame(points), use_container_width=True, hide_index=True)
    except (httpx.HTTPError, KeyError, ValueError) as exc:
        st.error(f"Could not load the observed sample from the API: {exc}")

with serpapi_tab:
    st.subheader("Flight offers collected through SerpApi")
    st.caption("A single API snapshot. These are offers available at collection time, not historical price observations.")
    st.markdown("Source: [SerpApi Google Flights API](https://serpapi.com/google-flights-api)")
    try:
        offers = load_serpapi_snapshot()
        if offers.empty:
            st.info("The SerpApi CSV has no flight rows.")
        else:
            collected = offers["observed_date"].dropna()
            if not collected.empty:
                st.info(f"Snapshot observation date: {collected.min()}")
            routes = sorted(offers["route_id"].unique())
            route = st.selectbox("Route", routes, key="serpapi_route")
            route_offers = offers[offers.route_id == route].copy()
            priced = route_offers.dropna(subset=["min_total_price"])
            a, b, c = st.columns(3)
            a.metric("Offers", int(route_offers.offer_count.sum()))
            if not priced.empty:
                b.metric("Lowest listed price", f"{priced.currency_code.iloc[0]} {priced.min_total_price.min():,.0f}")
                c.metric("Median of group medians", f"{priced.currency_code.iloc[0]} {priced.median_total_price.median():,.0f}")
            if not priced.empty:
                priced["departure_date"] = pd.to_datetime(priced["departure_date"])
                st.line_chart(priced.set_index("departure_date")[["min_total_price", "median_total_price"]])
            display_columns = [
                "departure_date", "return_date", "min_total_price", "median_total_price",
                "currency_code", "offer_count", "response_count",
            ]
            st.dataframe(
                route_offers[display_columns].sort_values("departure_date"),
                use_container_width=True, hide_index=True,
            )
            st.caption("These dbt mart results are grouped by departure date. The snapshot has one observation date, so it does not show price changes over time.")
    except (httpx.HTTPError, KeyError, ValueError) as exc:
        st.error(f"Could not load SerpApi flight marts from the API: {exc}")

with hotel_tab:
    st.subheader("Hotel offers collected through SerpApi")
    st.caption("A single API snapshot for a fixed three-night stay. These are current offers, not historical nightly prices.")
    st.markdown("Source: [SerpApi Google Hotels API](https://serpapi.com/google-hotels-api)")
    try:
        hotels = load_serpapi_hotels()
        if hotels.empty:
            st.info("The SerpApi hotel CSV has no rows.")
        else:
            collected = hotels["observed_date"].dropna()
            if not collected.empty:
                st.info(f"Snapshot observation date: {collected.min()}")
            destinations = sorted(hotels["city_iata"].dropna().unique())
            destination = st.selectbox("Destination", destinations, key="serpapi_hotel_destination")
            selection = hotels[hotels["city_iata"] == destination].copy()
            priced = selection.dropna(subset=["nightly_price"])
            a, b, c = st.columns(3)
            a.metric("Hotel offers", int(selection.observation_count.sum()))
            if not priced.empty:
                b.metric("Lowest nightly price", f"{priced.currency_code.iloc[0]} {priced.nightly_price.min():,.0f}")
                c.metric("Lowest stay total", f"{priced.currency_code.iloc[0]} {priced.total_price_egp.min():,.0f}")
            if not priced.empty:
                chart = priced.nsmallest(15, "nightly_price").set_index("hotel_name")[["nightly_price"]]
                st.bar_chart(chart)
            columns = ["hotel_name", "room_type", "hotel_class", "rating", "review_count",
                       "checkin_date", "checkout_date", "nightly_price", "total_price_egp",
                       "currency_code", "free_cancellation", "amenities"]
            st.dataframe(
                selection[columns].sort_values("nightly_price", na_position="last"),
                use_container_width=True, hide_index=True,
            )
            st.caption("These results come from the dbt hotel mart for a single observation date and one stay window; they do not show price changes over time.")
    except (httpx.HTTPError, KeyError, ValueError) as exc:
        st.error(f"Could not load SerpApi hotel marts from the API: {exc}")

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
