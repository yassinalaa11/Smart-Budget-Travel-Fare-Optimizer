import os
from datetime import date

import httpx
import pandas as pd
import streamlit as st

API_URL = os.getenv("SMARTFARE_API_URL", "http://localhost:8000").rstrip("/")

st.set_page_config(page_title="SmartFare", page_icon="✈️", layout="wide")
st.title("SmartFare")
st.caption("Travel-price intelligence platform: booking advisor, FX-normalized warehouse marts, and FastAPI ML inference.")


@st.cache_data(ttl=60)
def get_json(path: str) -> list[dict] | dict:
    response = httpx.get(f"{API_URL}{path}", timeout=15.0)
    response.raise_for_status()
    return response.json()


advisor_tab, observed_tab, serpapi_tab, hotel_tab, prediction_tab = st.tabs(
    ["Booking Advisor", "Observed flight prices", "SerpApi flights", "SerpApi hotels", "Prediction & ML Registry"]
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


with advisor_tab:
    st.subheader("Book Now vs. Wait & Combined Flight + Hotel Windows")
    try:
        routes = get_json("/api/v1/routes")
        if not routes:
            st.info("No warehouse routes available yet. Run ingestion and dbt first.")
        else:
            route_by_id = {r["route_id"]: r for r in routes}
            selected_route = st.selectbox(
                "Select route",
                options=list(route_by_id),
                key="advisor_route",
                format_func=lambda k: f"{route_by_id[k]['origin_iata']} → {route_by_id[k]['destination_iata']}",
            )
            departures = get_json(f"/api/v1/routes/{selected_route}/departures")
            if departures:
                labels = {
                    (
                        f"Depart {d['departure_date']} · "
                        + (f"return {d['return_date']}" if d["return_date"] else "one-way")
                    ): d
                    for d in departures
                }
                choice = st.selectbox("Select travel dates", list(labels), key="advisor_dates")
                chosen = labels[choice]
                rec_path = f"/api/v1/routes/{selected_route}/recommendation?departure_date={chosen['departure_date']}"
                if chosen["return_date"]:
                    rec_path += f"&return_date={chosen['return_date']}"
                rec = get_json(rec_path)

                if rec["contains_demo_data"]:
                    st.warning("Demo/mock observations included. Not live booking advice.")

                signal = rec["signal"]
                if signal == "BOOK_NOW":
                    st.success(f"**Recommendation: BOOK NOW** ({rec['model_stage']}) — {rec['reason']}")
                elif signal == "WAIT":
                    st.info(f"**Recommendation: WAIT** ({rec['model_stage']}) — {rec['reason']}")
                else:
                    st.warning(f"**Recommendation: NEUTRAL** ({rec['model_stage']}) — {rec['reason']}")

                c1, c2, c3, c4 = st.columns(4)
                c1.metric("Current Min Fare", f"{rec['currency_code']} {rec['current_price']}")
                c2.metric("Route Median", f"{rec['currency_code']} {rec['route_median_price']}")
                c3.metric("Percentile Rank", f"{int(rec['percentile_rank'] * 100)}th")
                c4.metric("Confidence", rec["confidence"])

                if rec.get("drop_probability") is not None:
                    q1, q2, q3 = st.columns(3)
                    q1.metric("Drop Probability (>=7%)", f"{rec['drop_probability'] * 100:.1f}%")
                    q2.metric("Expected Floor (P10–P50)", f"USD {rec['expected_min_price_p10']:,.0f} – {rec['expected_min_price_p50']:,.0f}")
                    q3.metric("Upper Band (P90)", f"USD {rec['expected_min_price_p90']:,.0f}")

            st.divider()
            st.markdown("**Cheapest Travel Windows (FX-Normalized to USD)**")
            windows_resp = get_json(f"/api/v1/routes/{selected_route}/cheapest-windows?limit=20")
            windows = windows_resp.get("windows", [])
            if windows:
                wdf = pd.DataFrame(windows)
                for col in [
                    "flight_price", "flight_price_usd", "hotel_total_price",
                    "hotel_total_price_usd", "combined_total_price", "combined_total_price_usd",
                ]:
                    if col in wdf:
                        wdf[col] = pd.to_numeric(wdf[col], errors="coerce")
                chart_cols = [c for c in ["flight_price_usd", "combined_total_price_usd"] if c in wdf and wdf[c].notna().any()]
                if chart_cols:
                    st.bar_chart(wdf.set_index("departure_date")[chart_cols])
                else:
                    st.bar_chart(wdf.set_index("departure_date")[["flight_price"]])
                st.dataframe(wdf, use_container_width=True, hide_index=True)
            else:
                st.info("No travel windows available for this route.")
    except (httpx.HTTPError, KeyError, ValueError) as exc:
        st.error(f"Could not load advisor data from API: {exc}")


with observed_tab:
    st.subheader("Collected flight-price history")
    st.caption("Observed provider sample stored in the Gold warehouse.")
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
                    if chart["observed_date"].nunique() > 1:
                        st.line_chart(chart.set_index("observed_date")[["min_total_price", "median_total_price"]])
                    else:
                        st.bar_chart(pd.DataFrame(
                            {"price": [float(latest["min_total_price"]), float(latest["median_total_price"])]},
                            index=["Minimum", "Median"],
                        ))
                        st.info("Only one observation day is available for this departure date.")
                    st.dataframe(pd.DataFrame(points), use_container_width=True, hide_index=True)
    except (httpx.HTTPError, KeyError, ValueError) as exc:
        st.error(f"Could not load the observed sample from the API: {exc}")


with serpapi_tab:
    st.subheader("Flight offers collected through SerpApi")
    st.caption("A single API snapshot served from Gold marts.")
    st.markdown("Source: [SerpApi Google Flights API](https://serpapi.com/google-flights-api)")
    try:
        offers = load_serpapi_snapshot()
        if offers.empty:
            st.info("The SerpApi flight mart has no rows.")
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
    except (httpx.HTTPError, KeyError, ValueError) as exc:
        st.error(f"Could not load SerpApi flight marts from the API: {exc}")


with hotel_tab:
    st.subheader("Hotel offers collected through SerpApi")
    st.caption("A single API snapshot for a fixed stay window served from Gold marts.")
    st.markdown("Source: [SerpApi Google Hotels API](https://serpapi.com/google-hotels-api)")
    try:
        hotels = load_serpapi_hotels()
        if hotels.empty:
            st.info("The SerpApi hotel mart has no rows.")
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
                chart = priced.nsmallest(15, "nightly_price").set_index("hotel_name")[["nightly_price"]]
                st.bar_chart(chart)
            columns = [
                "hotel_name", "room_type", "hotel_class", "rating", "review_count",
                "checkin_date", "checkout_date", "nightly_price", "total_price_egp",
                "currency_code", "free_cancellation", "amenities",
            ]
            st.dataframe(
                selection[columns].sort_values("nightly_price", na_position="last"),
                use_container_width=True, hide_index=True,
            )
    except (httpx.HTTPError, KeyError, ValueError) as exc:
        st.error(f"Could not load SerpApi hotel marts from the API: {exc}")


with prediction_tab:
    st.subheader("FastAPI ML Inference & Walk-Forward Model Registry")
    try:
        status = get_json("/api/v1/models/status")
        m1, m2, m3, m4 = st.columns(4)
        m1.metric("Model Version", status.get("model_version") or "Not trained")
        m2.metric("Live Warehouse Gate", status.get("live_model_status", "not_trained"))
        m3.metric("Live Promoted Routes", len(status.get("live_promoted_routes") or []))
        m4.metric("Synthetic Gate Status", status.get("synthetic_decision_status", "not_trained"))

        synth_dec = (status.get("scorecard") or {}).get("synthetic_decision_prototype") or {}
        g_metrics = synth_dec.get("global_metrics") or {}
        if g_metrics:
            st.markdown("**Walk-Forward Backtest Scorecard (Classifier + Quantile Regressors on Synthetic Prototype)**")
            b1, b2, b3, b4 = st.columns(4)
            b1.metric("Model Brier Score", g_metrics.get("model_brier"), delta=f"Base: {g_metrics.get('baseline_brier')}")
            b2.metric("ROC-AUC", g_metrics.get("roc_auc") or "N/A")
            b3.metric("P50 Quantile MAE", g_metrics.get("quantile_p50_mae"), delta=f"Base: {g_metrics.get('baseline_mae')}")
            b4.metric("Simulated Regret", g_metrics.get("model_traveler_regret"), delta=f"Base: {g_metrics.get('baseline_traveler_regret')}")

        if not status.get("artifacts_loaded"):
            st.warning("No serialized model artifacts loaded yet. Run: `docker compose exec ingestion-runner python -m ml.train` then reload.")
    except (httpx.HTTPError, KeyError, ValueError) as exc:
        st.error(f"Could not load model registry status from API: {exc}")

    st.divider()
    st.warning(
        "Lead-time curve demos below are served via FastAPI from serialized models trained on synthetic datasets. "
        "They are not booking advice or validated live forecasts."
    )
    mode = st.radio("Inference dataset", ["Flights", "Hotels"], horizontal=True)
    try:
        if mode == "Flights":
            opts = get_json("/api/v1/predict/flight/options")
            route = st.selectbox("Route", opts["routes"], key="demo_flight_route")
            depart_month = st.selectbox("Departure month", list(range(1, 13)), index=0)
            weekday = st.selectbox("Departure weekday (Monday = 0)", list(range(7)), index=4)
            pred = get_json(
                f"/api/v1/predict/flight?route_id={route}&departure_month={depart_month}&departure_weekday={weekday}"
            )
            curve = pd.DataFrame(pred["curve"])
            currency = pred["currency"]
            metrics = pred["metrics"]
            x, y, z = st.columns(3)
            x.metric("Lowest model estimate", f"{currency} {pred['lowest_predicted_price']:,.0f}", f"about {pred['best_days_to_departure']} days before")
            y.metric("Holdout MAE", f"{currency} {metrics['mae']:,.1f}")
            z.metric("Baseline MAE", f"{currency} {metrics['baseline_mae']:,.1f}")
            st.caption(f"Chronological holdout: {metrics['train_rows']:,} train rows, {metrics['test_rows']:,} test rows.")
            st.line_chart(curve.set_index("days_to_departure")[["predicted_price"]])
            st.dataframe(curve, use_container_width=True, hide_index=True)
        else:
            hotels_list = get_json("/api/v1/predict/hotel/options")
            hotel_map = {h["hotel_id"]: h for h in hotels_list}
            hotel_id = st.selectbox(
                "Hotel",
                list(hotel_map.keys()),
                format_func=lambda k: f"{hotel_map[k]['name']} · {hotel_map[k]['city']}",
            )
            arrival = st.date_input("Check-in date", value=date(2027, 1, 15), min_value=date.today())
            nights = st.number_input("Nights", min_value=1, max_value=30, value=3)
            adults = st.number_input("Adults", min_value=1, max_value=8, value=2)
            pred = get_json(
                f"/api/v1/predict/hotel?hotel_id={hotel_id}&checkin_month={arrival.month}"
                f"&checkin_weekday={arrival.weekday()}&nights={int(nights)}&adults={int(adults)}"
            )
            curve = pd.DataFrame(pred["curve"])
            metrics = pred["metrics"]
            x, y, z = st.columns(3)
            x.metric("Lowest estimated nightly", f"USD {pred['lowest_predicted_nightly']:,.0f}", f"about {pred['best_days_to_checkin']} days before")
            y.metric("Estimated stay total", f"USD {pred['lowest_predicted_stay_total']:,.0f}")
            z.metric("Holdout MAE / night", f"USD {metrics['mae']:,.1f}")
            st.caption(f"Chronological holdout: {metrics['train_rows']:,} train rows, {metrics['test_rows']:,} test rows. Baseline MAE: USD {metrics['baseline_mae']:,.1f}/night.")
            st.line_chart(curve.set_index("days_to_checkin")[["predicted_nightly"]])
            st.dataframe(curve, use_container_width=True, hide_index=True)
    except (httpx.HTTPError, KeyError, ValueError) as exc:
        st.error(f"Could not query FastAPI prediction endpoint: {exc}")
