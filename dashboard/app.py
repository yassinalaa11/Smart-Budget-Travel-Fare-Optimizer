import os
from datetime import date
from urllib.parse import urlencode

import httpx
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

API_URL = os.getenv("SMARTFARE_API_URL", "http://localhost:8000").rstrip("/")
PUBLIC_API_URL = os.getenv("SMARTFARE_PUBLIC_API_URL", "http://localhost:8000").rstrip("/")

st.set_page_config(page_title="SmartFare", page_icon="✈️", layout="wide")
st.title("SmartFare")
st.caption("Travel-price intelligence platform: booking advisor, Plotly analytics, affiliate attribution, price-drop alerts, and FastAPI ML inference.")


@st.cache_data(ttl=60)
def get_json(path: str) -> list[dict] | dict:
    response = httpx.get(f"{API_URL}{path}", timeout=15.0)
    response.raise_for_status()
    return response.json()


def get_json_live(path: str) -> list[dict] | dict:
    response = httpx.get(f"{API_URL}{path}", timeout=15.0)
    response.raise_for_status()
    return response.json()


def build_tracked_booking_url(
    *,
    route_id: str,
    departure_date: str,
    return_date: str | None,
    partner: str,
    quoted_price: str | float | None = None,
    quoted_currency: str | None = None,
    source_tab: str = "booking_advisor",
) -> str:
    params: dict[str, str] = {
        "route_id": route_id,
        "departure_date": departure_date,
        "partner": partner,
        "source_tab": source_tab,
    }
    if return_date:
        params["return_date"] = return_date
    if quoted_price is not None:
        params["quoted_price"] = str(quoted_price)
    if quoted_currency:
        params["quoted_currency"] = quoted_currency
    return f"{PUBLIC_API_URL}/api/v1/redirect/book?{urlencode(params)}"


def render_booking_curve_figure(
    curve_resp: dict,
    *,
    current_lead_days: int | None = None,
    current_price_usd: float | None = None,
) -> go.Figure:
    points = curve_resp.get("points", [])
    df = pd.DataFrame(points).sort_values("days_to_departure")

    fig = go.Figure()
    fig.add_trace(
        go.Scatter(
            x=df["days_to_departure"],
            y=df["p75_price_usd"],
            mode="lines",
            line={"width": 0},
            name="75th Percentile",
            showlegend=False,
            hovertemplate="Lead: %{x}d<br>P75: $%{y:,.0f}<extra></extra>",
        )
    )
    fig.add_trace(
        go.Scatter(
            x=df["days_to_departure"],
            y=df["p25_price_usd"],
            mode="lines",
            line={"width": 0},
            fill="tonexty",
            fillcolor="rgba(59, 130, 246, 0.22)",
            name="25th–75th Percentile Cone",
            hovertemplate="Lead: %{x}d<br>P25: $%{y:,.0f}<extra></extra>",
        )
    )
    fig.add_trace(
        go.Scatter(
            x=df["days_to_departure"],
            y=df["median_price_usd"],
            mode="lines+markers",
            line={"color": "#1d4ed8", "width": 2.5},
            marker={"size": 5},
            name="Median Fare (USD)",
            hovertemplate="Lead: %{x}d<br>Median: $%{y:,.0f}<extra></extra>",
        )
    )
    fig.add_trace(
        go.Scatter(
            x=df["days_to_departure"],
            y=df["min_price_usd"],
            mode="lines",
            line={"color": "#16a34a", "width": 1.8, "dash": "dot"},
            name="Observed Floor (USD)",
            hovertemplate="Lead: %{x}d<br>Min: $%{y:,.0f}<extra></extra>",
        )
    )

    if current_lead_days is not None and current_price_usd is not None:
        fig.add_trace(
            go.Scatter(
                x=[current_lead_days],
                y=[current_price_usd],
                mode="markers",
                marker={"color": "#dc2626", "size": 12, "symbol": "diamond", "line": {"color": "#ffffff", "width": 1.5}},
                name="Selected Trip Quote",
                hovertemplate="Selected Trip<br>Lead: %{x}d<br>USD %{y:,.2f}<extra></extra>",
            )
        )

    fig.update_layout(
        title=f"Historical Booking Curve — {curve_resp['route_id']}",
        xaxis_title="Days Before Departure (Countdown →)",
        yaxis_title="Fare (USD)",
        xaxis={"autorange": "reversed"},
        hovermode="x unified",
        height=420,
        margin={"l": 20, "r": 20, "t": 48, "b": 20},
        legend={"orientation": "h", "yanchor": "bottom", "y": 1.02, "xanchor": "right", "x": 1},
    )
    return fig


def render_trip_heatmap_figure(heatmap_resp: dict) -> go.Figure:
    cells = heatmap_resp.get("cells", [])
    df = pd.DataFrame(cells)
    pivot = df.pivot(index="stay_nights", columns="departure_date", values="total_trip_usd")
    pivot = pivot.sort_index()
    pivot.index = [f"{n} nights" for n in pivot.index]

    flight_piv = df.pivot(index="stay_nights", columns="departure_date", values="flight_price_usd").sort_index()
    hotel_piv = df.pivot(index="stay_nights", columns="departure_date", values="hotel_total_usd").sort_index()

    custom = []
    for i in range(len(pivot.index)):
        row_data = []
        for j in range(len(pivot.columns)):
            f_val = flight_piv.iloc[i, j]
            h_val = hotel_piv.iloc[i, j]
            h_str = f"${h_val:,.0f}" if pd.notna(h_val) else "N/A"
            row_data.append(f"Flight: ${f_val:,.0f}<br>Hotel: {h_str}")
        custom.append(row_data)

    fig = go.Figure(
        data=go.Heatmap(
            z=pivot.values,
            x=[str(c) for c in pivot.columns],
            y=list(pivot.index),
            colorscale="YlGnBu_r",
            text=pivot.values,
            texttemplate="$%{text:,.0f}",
            customdata=custom,
            hovertemplate="Depart: %{x}<br>Stay: %{y}<br>Total: $%{z:,.0f}<br>%{customdata}<extra></extra>",
            colorbar={"title": "Total USD"},
        )
    )
    fig.update_layout(
        title=f"Total Trip Cost Heatmap (Flight + Hotel USD) — {heatmap_resp['route_id']}",
        xaxis_title="Departure Date",
        yaxis_title="Length of Stay",
        height=360,
        margin={"l": 20, "r": 20, "t": 48, "b": 20},
    )
    return fig


(
    advisor_tab,
    alerts_tab,
    observed_tab,
    serpapi_tab,
    hotel_tab,
    prediction_tab,
) = st.tabs(
    [
        "Booking Advisor",
        "Price Alerts & Affiliate",
        "Observed Flights",
        "SerpApi Flights",
        "SerpApi Hotels",
        "ML Registry & Forecasts",
    ]
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
    st.subheader("Book Now vs. Wait, Booking Curve Cone & Total Trip Cost Heatmap")
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
            current_lead: int | None = None
            current_usd: float | None = None
            chosen: dict | None = None

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
                current_lead = int(rec["days_to_departure"])

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

                st.markdown("**Tracked Affiliate Booking Deep-Links** (logs `sub_id` click attribution)")
                b1, b2, b3 = st.columns(3)
                b1.link_button(
                    "Book on Aviasales",
                    build_tracked_booking_url(
                        route_id=selected_route,
                        departure_date=chosen["departure_date"],
                        return_date=chosen["return_date"],
                        partner="aviasales",
                        quoted_price=rec["current_price"],
                        quoted_currency=rec["currency_code"],
                    ),
                    use_container_width=True,
                )
                b2.link_button(
                    "Compare on Google Flights",
                    build_tracked_booking_url(
                        route_id=selected_route,
                        departure_date=chosen["departure_date"],
                        return_date=chosen["return_date"],
                        partner="google_flights",
                        quoted_price=rec["current_price"],
                        quoted_currency=rec["currency_code"],
                    ),
                    use_container_width=True,
                )
                b3.link_button(
                    "Search on Skyscanner",
                    build_tracked_booking_url(
                        route_id=selected_route,
                        departure_date=chosen["departure_date"],
                        return_date=chosen["return_date"],
                        partner="skyscanner",
                        quoted_price=rec["current_price"],
                        quoted_currency=rec["currency_code"],
                    ),
                    use_container_width=True,
                )

            st.divider()
            windows_resp = get_json(f"/api/v1/routes/{selected_route}/cheapest-windows?limit=20")
            windows = windows_resp.get("windows", [])
            if windows and chosen is not None:
                for w in windows:
                    if w["departure_date"] == chosen["departure_date"] and w.get("flight_price_usd") is not None:
                        current_usd = float(w["flight_price_usd"])
                        break

            col_curve, col_heat = st.columns(2)
            with col_curve:
                curve_resp = get_json(f"/api/v1/routes/{selected_route}/booking-curve?source=auto")
                if curve_resp.get("points"):
                    st.plotly_chart(
                        render_booking_curve_figure(
                            curve_resp,
                            current_lead_days=current_lead,
                            current_price_usd=current_usd,
                        ),
                        use_container_width=True,
                    )
                    if curve_resp.get("is_synthetic"):
                        st.caption(f"Source: `{curve_resp['source']}` (3-year synthetic reference cone while live daily history accumulates).")
                    else:
                        st.caption(f"Source: `{curve_resp['source']}` (live warehouse observations).")
                else:
                    st.info("No booking curve observations available for this route yet.")

            with col_heat:
                heatmap_resp = get_json(f"/api/v1/routes/{selected_route}/trip-heatmap?stay_nights=3,5,7,10")
                if heatmap_resp.get("cells"):
                    st.plotly_chart(render_trip_heatmap_figure(heatmap_resp), use_container_width=True)
                    st.caption("Combines latest USD flight quote with destination median nightly hotel rate across stay lengths.")
                else:
                    st.info("No combined Flight + Hotel heatmap cells available for this route.")

            st.divider()
            st.markdown("**Cheapest Travel Windows (FX-Normalized to USD)**")
            if windows:
                wdf = pd.DataFrame(windows)
                for col in [
                    "flight_price", "flight_price_usd", "hotel_total_price",
                    "hotel_total_price_usd", "combined_total_price", "combined_total_price_usd",
                ]:
                    if col in wdf:
                        wdf[col] = pd.to_numeric(wdf[col], errors="coerce")
                st.dataframe(wdf, use_container_width=True, hide_index=True)
            else:
                st.info("No travel windows available for this route.")
    except (httpx.HTTPError, KeyError, ValueError) as exc:
        st.error(f"Could not load advisor data from API: {exc}")


with alerts_tab:
    st.subheader("Price Drop Alerts & Affiliate Revenue Attribution")
    col_alert, col_aff = st.columns(2)

    with col_alert:
        st.markdown("### Create Price Drop Alert")
        try:
            routes_for_alert = get_json("/api/v1/routes")
            route_options = [r["route_id"] for r in routes_for_alert] if routes_for_alert else ["CAI-DXB"]
            with st.form("create_alert_form"):
                a_route = st.selectbox("Route", route_options, key="alert_route_select")
                a_dep = st.date_input("Departure date", value=date(2027, 1, 14))
                a_target = st.number_input("Target fare (USD)", min_value=10.0, max_value=5000.0, value=200.0, step=5.0)
                a_channel = st.selectbox("Notification channel", ["all", "slack", "telegram", "email"])
                a_contact = st.text_input("Optional contact / label", placeholder="name@example.com or Telegram handle")
                submitted = st.form_submit_button("Create Alert", use_container_width=True)
                if submitted:
                    resp = httpx.post(
                        f"{API_URL}/api/v1/alerts",
                        json={
                            "route_id": a_route,
                            "departure_date": a_dep.isoformat(),
                            "target_price_usd": a_target,
                            "channel": a_channel,
                            "contact": a_contact or None,
                        },
                        timeout=15.0,
                    )
                    resp.raise_for_status()
                    st.success(f"Alert created for {a_route} on {a_dep} at <= USD {a_target:.2f}.")

            if st.button("Run Price Alert Check Now", use_container_width=True):
                check_resp = httpx.post(f"{API_URL}/api/v1/alerts/check", timeout=20.0)
                check_resp.raise_for_status()
                res = check_resp.json()
                st.info(f"Evaluated {res['checked']} active alert(s); triggered {res['triggered']}.")

            active_alerts = get_json_live("/api/v1/alerts?active_only=true")
            if active_alerts:
                st.dataframe(pd.DataFrame(active_alerts), use_container_width=True, hide_index=True)
                del_options = {
                    f"{a['route_id']} · {a['departure_date']} · <= USD {a['target_price_usd']}": a["alert_id"]
                    for a in active_alerts
                }
                chosen_del = st.selectbox("Select alert to deactivate", list(del_options), key="deactivate_alert_sel")
                if st.button("Deactivate Selected Alert"):
                    del_resp = httpx.delete(f"{API_URL}/api/v1/alerts/{del_options[chosen_del]}", timeout=15.0)
                    del_resp.raise_for_status()
                    st.success("Alert deactivated.")
                    st.rerun()
            else:
                st.info("No active price alerts configured yet.")
        except (httpx.HTTPError, KeyError, ValueError) as exc:
            st.error(f"Alert API error: {exc}")

    with col_aff:
        st.markdown("### Affiliate Click & Commission Attribution")
        try:
            stats = get_json_live("/api/v1/affiliate/stats?limit=25")
            k1, k2, k3 = st.columns(3)
            k1.metric("Tracked Clicks", stats["total_clicks"])
            k2.metric("Conversions", stats["confirmed_conversions"], f"{stats['conversion_rate_pct']:.1f}%")
            k3.metric("RPM (USD / 1k clicks)", f"${stats['rpm_usd']:,.2f}", f"Comm: ${stats['total_commission_usd']:,.2f}")

            recent_clicks = stats.get("recent_clicks", [])
            if recent_clicks:
                st.dataframe(pd.DataFrame(recent_clicks), use_container_width=True, hide_index=True)
                unconverted = [c["sub_id"] for c in recent_clicks if c["conversion_status"] != "confirmed"]
                if unconverted:
                    with st.expander("Simulate Affiliate Conversion Webhook"):
                        with st.form("conv_webhook_form"):
                            c_sub = st.selectbox("Click sub_id", unconverted)
                            c_val = st.number_input("Booking Value (USD)", min_value=20.0, max_value=10000.0, value=240.0, step=10.0)
                            c_comm = st.number_input("Commission (USD)", min_value=0.0, max_value=1000.0, value=7.20, step=0.5)
                            if st.form_submit_button("Record Confirmed Conversion"):
                                w_resp = httpx.post(
                                    f"{API_URL}/api/v1/affiliate/conversions",
                                    json={
                                        "sub_id": c_sub,
                                        "booking_value_usd": c_val,
                                        "commission_usd": c_comm,
                                        "status": "confirmed",
                                    },
                                    timeout=15.0,
                                )
                                w_resp.raise_for_status()
                                st.success(f"Attributed USD {c_comm:.2f} commission to {c_sub}.")
                                st.rerun()
            else:
                st.info("Click any affiliate booking link in the Booking Advisor tab to generate tracked click events.")
        except (httpx.HTTPError, KeyError, ValueError) as exc:
            st.error(f"Affiliate stats API error: {exc}")


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
                        fig = px.line(
                            chart,
                            x="observed_date",
                            y=["min_total_price", "median_total_price"],
                            markers=True,
                            title=f"Observed Fare Trend — {selected_route}",
                        )
                        st.plotly_chart(fig, use_container_width=True)
                    else:
                        fig = px.bar(
                            pd.DataFrame({
                                "Metric": ["Minimum", "Median"],
                                "Price": [float(latest["min_total_price"]), float(latest["median_total_price"])],
                            }),
                            x="Metric",
                            y="Price",
                            title=f"Single-Day Snapshot ({latest['currency_code']})",
                        )
                        st.plotly_chart(fig, use_container_width=True)
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
                fig = px.line(
                    priced.sort_values("departure_date"),
                    x="departure_date",
                    y=["min_total_price", "median_total_price"],
                    markers=True,
                    title=f"SerpApi Snapshot by Departure Date — {route}",
                )
                st.plotly_chart(fig, use_container_width=True)
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
                top15 = priced.nsmallest(15, "nightly_price").sort_values("nightly_price")
                fig = px.bar(
                    top15,
                    x="nightly_price",
                    y="hotel_name",
                    orientation="h",
                    title=f"Lowest 15 Nightly Hotel Rates — {destination}",
                )
                fig.update_layout(height=420, yaxis_title="", xaxis_title=f"Nightly Price ({priced.currency_code.iloc[0]})")
                st.plotly_chart(fig, use_container_width=True)
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
            st.markdown("**Walk-Forward Backtest Scorecard (Classifier + Quantile Regressors)**")
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
            fig = px.line(
                curve,
                x="days_to_departure",
                y="predicted_price",
                markers=True,
                title=f"Predicted Flight Lead-Time Curve — {route} ({currency})",
            )
            fig.update_xaxes(autorange="reversed", title="Days Before Departure")
            fig.update_yaxes(title=f"Predicted Price ({currency})")
            st.plotly_chart(fig, use_container_width=True)
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
            fig = px.line(
                curve,
                x="days_to_checkin",
                y="predicted_nightly",
                markers=True,
                title=f"Predicted Hotel Nightly Curve — {pred['hotel_name']} (USD)",
            )
            fig.update_xaxes(autorange="reversed", title="Days Before Check-in")
            fig.update_yaxes(title="Predicted Nightly Rate (USD)")
            st.plotly_chart(fig, use_container_width=True)
            st.dataframe(curve, use_container_width=True, hide_index=True)
    except (httpx.HTTPError, KeyError, ValueError) as exc:
        st.error(f"Could not query FastAPI prediction endpoint: {exc}")
