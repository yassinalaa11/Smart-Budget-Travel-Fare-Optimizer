from __future__ import annotations

import hashlib
from pathlib import Path

import numpy as np
import pandas as pd

DECISION_CATEGORICAL_FEATURES = ["route_id"]
DECISION_NUMERIC_FEATURES = [
    "days_to_departure",
    "departure_month",
    "departure_day_of_week",
    "day_of_year_sin",
    "day_of_year_cos",
    "day_of_week_sin",
    "day_of_week_cos",
    "price_ratio_to_route_mean",
    "price_velocity_3d",
    "price_velocity_7d",
    "z_score_vs_route_horizon",
    "markup_over_historical_min",
    "drawdown_from_peak",
    "is_weekend",
]


def _lead_bucket(days: pd.Series) -> pd.Series:
    bins = [-1, 7, 14, 21, 45, 90, 10000]
    labels = ["0-7d", "8-14d", "15-21d", "22-45d", "46-90d", "90d+"]
    return pd.cut(days, bins=bins, labels=labels).astype(str)


def engineer_flight_decision_features(df: pd.DataFrame) -> pd.DataFrame:
    frame = df.copy()
    frame["observed_date"] = pd.to_datetime(frame["observed_date"]).dt.normalize()
    frame["departure_date"] = pd.to_datetime(frame["departure_date"]).dt.normalize()
    frame["price"] = pd.to_numeric(frame["price"], errors="coerce")
    frame = frame.dropna(subset=["route_id", "observed_date", "departure_date", "price"])
    frame = frame[frame["price"] > 0].copy()

    if "days_to_departure" not in frame.columns:
        frame["days_to_departure"] = (frame["departure_date"] - frame["observed_date"]).dt.days
    frame["days_to_departure"] = pd.to_numeric(frame["days_to_departure"], errors="coerce")
    frame = frame[frame["days_to_departure"] >= 0].copy()

    frame = frame.sort_values(["route_id", "departure_date", "observed_date"]).reset_index(drop=True)

    frame["departure_month"] = frame["departure_date"].dt.month.astype(int)
    frame["departure_day_of_week"] = frame["departure_date"].dt.dayofweek.astype(int)
    doy = frame["departure_date"].dt.dayofyear.astype(float)
    dow = (frame["departure_day_of_week"] + 1).astype(float)
    frame["day_of_year_sin"] = np.sin(2.0 * np.pi * doy / 365.25)
    frame["day_of_year_cos"] = np.cos(2.0 * np.pi * doy / 365.25)
    frame["day_of_week_sin"] = np.sin(2.0 * np.pi * dow / 7.0)
    frame["day_of_week_cos"] = np.cos(2.0 * np.pi * dow / 7.0)
    frame["is_weekend"] = frame["departure_day_of_week"].isin([5, 6]).astype(int)
    frame["lead_time_bucket"] = _lead_bucket(frame["days_to_departure"])

    route_mean = frame.groupby("route_id")["price"].transform("mean")
    frame["price_ratio_to_route_mean"] = frame["price"] / route_mean.replace(0, np.nan)

    group_keys = ["route_id", "departure_date"]
    if "price_velocity_3d" not in frame.columns or frame["price_velocity_3d"].isna().all():
        lag3 = frame.groupby(group_keys)["price"].shift(3)
        frame["price_velocity_3d"] = (frame["price"] - lag3) / lag3.replace(0, np.nan)
    if "price_velocity_7d" not in frame.columns or frame["price_velocity_7d"].isna().all():
        lag7 = frame.groupby(group_keys)["price"].shift(7)
        frame["price_velocity_7d"] = (frame["price"] - lag7) / lag7.replace(0, np.nan)

    hist_min = frame.groupby(group_keys)["price"].cummin()
    hist_max = frame.groupby(group_keys)["price"].cummax()
    frame["markup_over_historical_min"] = frame["price"] / hist_min.replace(0, np.nan)
    frame["drawdown_from_peak"] = (hist_max - frame["price"]) / hist_max.replace(0, np.nan)

    rh_mean = frame.groupby(["route_id", "departure_month", "lead_time_bucket"])["price"].transform("mean")
    rh_std = frame.groupby(["route_id", "departure_month", "lead_time_bucket"])["price"].transform("std")
    frame["z_score_vs_route_horizon"] = np.where(
        (rh_std.notna()) & (rh_std > 0),
        (frame["price"] - rh_mean) / rh_std,
        0.0,
    )

    for col in DECISION_NUMERIC_FEATURES:
        frame[col] = pd.to_numeric(frame[col], errors="coerce").fillna(0.0)

    return frame


def build_forward_targets(
    frame: pd.DataFrame,
    *,
    horizon_days: int = 14,
    drop_threshold: float = 0.07,
) -> pd.DataFrame:
    df = frame.sort_values(["route_id", "departure_date", "observed_date"]).reset_index(drop=True)
    future_mins = np.full(len(df), np.nan, dtype=float)

    for _, grp in df.groupby(["route_id", "departure_date"], sort=False):
        idxs = grp.index.to_numpy()
        obs_dates = grp["observed_date"].to_numpy()
        prices = grp["price"].to_numpy(dtype=float)
        n = len(idxs)
        for i in range(n - 1):
            t_i = obs_dates[i]
            cutoff = t_i + np.timedelta64(horizon_days, "D")
            window_prices = []
            for j in range(i + 1, n):
                if obs_dates[j] > cutoff:
                    break
                if obs_dates[j] > t_i:
                    window_prices.append(prices[j])
            if window_prices:
                future_mins[idxs[i]] = float(min(window_prices))

    df["y_min_future_price"] = future_mins
    df["y_will_drop"] = np.where(
        np.isnan(future_mins),
        np.nan,
        (future_mins <= (1.0 - drop_threshold) * df["price"].to_numpy(dtype=float)).astype(float),
    )
    return df


def load_synthetic_flights(data_dir: Path) -> tuple[pd.DataFrame, str]:
    hist_path = data_dir / "historical_flights_3yrs.csv"
    if hist_path.exists():
        raw = pd.read_csv(hist_path)
        raw["observed_at"] = pd.to_datetime(raw["collected_at"], utc=True, format="mixed")
        raw["observed_date"] = raw["observed_at"].dt.tz_localize(None).dt.normalize()
        raw["departure_date"] = pd.to_datetime(raw["flight_date"]).dt.normalize()
        raw["route_id"] = raw["origin"].astype(str).str.upper() + "-" + raw["destination"].astype(str).str.upper()

        if "price_usd" in raw.columns and raw["price_usd"].notna().any():
            raw["price"] = pd.to_numeric(raw["price_usd"], errors="coerce")
            currency = "USD"
        else:
            raw["price"] = pd.to_numeric(raw["price_egp"], errors="coerce")
            currency = "EGP"

        raw = raw.dropna(subset=["route_id", "observed_date", "departure_date", "price"])
        raw = raw[raw["price"] > 0]

        # Aggregate to route-day-departure minimum fare (matches fct_flight_price_observations grain)
        frame = (
            raw.groupby(["route_id", "observed_date", "departure_date"], as_index=False)
            .agg(price=("price", "min"))
        )
        frame["currency"] = currency
        frame["days_to_departure"] = (frame["departure_date"] - frame["observed_date"]).dt.days
        frame["departure_month"] = frame["departure_date"].dt.month
        frame["departure_weekday"] = frame["departure_date"].dt.dayofweek
        frame = frame[frame["days_to_departure"] >= 0].reset_index(drop=True)
        return frame, hist_path.name

    calib_path = data_dir / "synthetic_flights_calibrated.csv"
    frame = pd.read_csv(calib_path)
    frame["observed_at"] = pd.to_datetime(frame["observed_at"], utc=True)
    frame["observed_date"] = frame["observed_at"].dt.tz_localize(None).dt.normalize()
    frame["departure_date"] = pd.to_datetime(frame["departure_date"]).dt.normalize()
    frame["route_id"] = frame["origin"].astype(str) + "-" + frame["destination"].astype(str)
    frame["days_to_departure"] = pd.to_numeric(frame["days_to_departure"], errors="coerce")
    frame["price"] = pd.to_numeric(frame["price"], errors="coerce")
    frame["departure_month"] = frame["departure_date"].dt.month
    frame["departure_weekday"] = frame["departure_date"].dt.dayofweek
    return frame, calib_path.name


def load_synthetic_hotels(data_dir: Path) -> tuple[pd.DataFrame, pd.DataFrame, str]:
    hist_path = data_dir / "historical_hotels_3yrs.csv"
    if hist_path.exists():
        raw = pd.read_csv(hist_path)
        raw["observed_at"] = pd.to_datetime(raw["collected_at"], utc=True, format="mixed")
        raw["observed_date"] = raw["observed_at"].dt.tz_localize(None).dt.normalize()
        raw["check_in"] = pd.to_datetime(raw["check_in"]).dt.normalize()
        raw["check_out"] = pd.to_datetime(raw["check_out"]).dt.normalize()
        raw["destination"] = raw["destination"].astype(str).str.upper()
        raw["name"] = raw["name"].astype(str).str.strip()

        if "price_per_night_usd" in raw.columns and raw["price_per_night_usd"].notna().any():
            raw["price_per_night"] = pd.to_numeric(raw["price_per_night_usd"], errors="coerce")
        else:
            raw["price_per_night"] = pd.to_numeric(raw["price_per_night_egp"], errors="coerce")

        raw = raw.dropna(subset=["destination", "name", "observed_date", "check_in", "check_out", "price_per_night"])
        raw = raw[raw["price_per_night"] > 0].copy()

        raw["hotel_id"] = [
            hashlib.sha256(f"{d}\0{n}".encode()).hexdigest()[:24]
            for d, n in zip(raw["destination"], raw["name"])
        ]
        raw["city"] = raw["destination"]
        raw["stars"] = pd.to_numeric(raw.get("hotel_class"), errors="coerce").fillna(3.0)
        raw["nights"] = (raw["check_out"] - raw["check_in"]).dt.days.clip(lower=1)
        raw["adults"] = 2
        raw["days_to_checkin"] = (raw["check_in"] - raw["observed_date"]).dt.days
        raw["checkin_month"] = raw["check_in"].dt.month
        raw["checkin_weekday"] = raw["check_in"].dt.dayofweek
        raw = raw[raw["days_to_checkin"] >= 0].reset_index(drop=True)

        hotels = (
            raw.groupby("hotel_id", as_index=False)
            .agg(name=("name", "first"), city=("city", "first"), stars=("stars", "median"))
            .sort_values(["city", "name"])
            .reset_index(drop=True)
        )
        return raw, hotels, hist_path.name

    hotels = pd.read_csv(data_dir / "synthetic_hotels.csv")
    prices = pd.read_csv(data_dir / "synthetic_hotel_prices.csv")
    prices["observed_at"] = pd.to_datetime(prices["observed_at"], utc=True)
    prices["observed_date"] = prices["observed_at"].dt.tz_localize(None).dt.normalize()
    prices["check_in"] = pd.to_datetime(prices["check_in"]).dt.normalize()
    prices["checkin_month"] = prices["check_in"].dt.month
    prices["checkin_weekday"] = prices["check_in"].dt.dayofweek
    prices["days_to_checkin"] = pd.to_numeric(prices["days_to_checkin"], errors="coerce")
    prices["nights"] = pd.to_numeric(prices["nights"], errors="coerce")
    prices["adults"] = pd.to_numeric(prices["adults"], errors="coerce")
    prices["price_per_night"] = pd.to_numeric(prices["price_per_night"], errors="coerce")
    prices["hotel_id"] = prices["hotel_id"].astype(str)
    hotels["hotel_id"] = hotels["hotel_id"].astype(str)
    frame = prices.merge(hotels, on="hotel_id", how="inner")
    frame = frame[frame["available"].astype(str).str.lower().isin(["true", "1"])]
    frame = frame.dropna(subset=["price_per_night"])
    return frame, hotels, "synthetic_hotel_prices.csv"


def build_single_decision_row(
    *,
    route_id: str,
    observed_date: pd.Timestamp,
    departure_date: pd.Timestamp,
    price: float,
    route_mean_price: float,
    price_velocity_3d: float | None = None,
    price_velocity_7d: float | None = None,
    z_score_vs_route_horizon: float | None = None,
    markup_over_historical_min: float | None = None,
    drawdown_from_peak: float | None = None,
) -> pd.DataFrame:
    days_to_departure = max(int((departure_date - observed_date).days), 0)
    dep_dow = int(departure_date.dayofweek)
    doy = float(departure_date.dayofyear)
    dow = float(dep_dow + 1)
    return pd.DataFrame(
        [
            {
                "route_id": route_id.upper(),
                "days_to_departure": float(days_to_departure),
                "departure_month": float(departure_date.month),
                "departure_day_of_week": float(dep_dow),
                "day_of_year_sin": float(np.sin(2.0 * np.pi * doy / 365.25)),
                "day_of_year_cos": float(np.cos(2.0 * np.pi * doy / 365.25)),
                "day_of_week_sin": float(np.sin(2.0 * np.pi * dow / 7.0)),
                "day_of_week_cos": float(np.cos(2.0 * np.pi * dow / 7.0)),
                "price_ratio_to_route_mean": float(price / route_mean_price) if route_mean_price > 0 else 1.0,
                "price_velocity_3d": float(price_velocity_3d or 0.0),
                "price_velocity_7d": float(price_velocity_7d or 0.0),
                "z_score_vs_route_horizon": float(z_score_vs_route_horizon or 0.0),
                "markup_over_historical_min": float(markup_over_historical_min or 1.0),
                "drawdown_from_peak": float(drawdown_from_peak or 0.0),
                "is_weekend": float(1 if dep_dow in (5, 6) else 0),
            }
        ]
    )
