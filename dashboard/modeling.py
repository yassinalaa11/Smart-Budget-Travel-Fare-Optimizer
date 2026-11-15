"""Small, explainable demo models trained only on the supplied synthetic CSVs."""

from __future__ import annotations

from pathlib import Path

import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.ensemble import RandomForestRegressor
from sklearn.metrics import mean_absolute_error
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder

DATA_DIR = Path("/app/data")


def _pipeline(categorical: list[str], numeric: list[str]) -> Pipeline:
    prep = ColumnTransformer(
        [
            ("categories", OneHotEncoder(handle_unknown="ignore"), categorical),
            ("numbers", "passthrough", numeric),
        ]
    )
    return Pipeline(
        [
            ("features", prep),
            ("model", RandomForestRegressor(n_estimators=60, max_depth=18,
                                             min_samples_leaf=3, random_state=42, n_jobs=-1)),
        ]
    )


def _temporal_split(frame: pd.DataFrame, date_column: str) -> tuple[pd.DataFrame, pd.DataFrame]:
    dates = sorted(frame[date_column].dropna().unique())
    if len(dates) < 2:
        raise ValueError("At least two observation dates are required for a time-based holdout.")
    first_test = dates[max(1, int(len(dates) * 0.8))]
    train = frame[frame[date_column] < first_test]
    test = frame[frame[date_column] >= first_test]
    if train.empty or test.empty:
        raise ValueError("Could not create a chronological train/test split.")
    return train, test


def load_flights() -> pd.DataFrame:
    frame = pd.read_csv(DATA_DIR / "synthetic_flights_calibrated.csv")
    frame["observed_at"] = pd.to_datetime(frame["observed_at"], utc=True)
    frame["departure_date"] = pd.to_datetime(frame["departure_date"])
    frame["route_id"] = frame["origin"].astype(str) + "-" + frame["destination"].astype(str)
    frame["observation_day"] = frame["observed_at"].dt.date
    frame["departure_month"] = frame["departure_date"].dt.month
    frame["departure_weekday"] = frame["departure_date"].dt.dayofweek
    frame["days_to_departure"] = pd.to_numeric(frame["days_to_departure"])
    frame["price"] = pd.to_numeric(frame["price"])
    return frame


def train_flight_model(frame: pd.DataFrame) -> tuple[Pipeline, dict[str, float]]:
    cats = ["route_id"]
    nums = ["days_to_departure", "departure_month", "departure_weekday"]
    train, test = _temporal_split(frame, "observation_day")
    model = _pipeline(cats, nums)
    model.fit(train[cats + nums], train["price"])
    pred = model.predict(test[cats + nums])
    baseline = train.groupby("route_id")["price"].median()
    baseline_pred = test["route_id"].map(baseline).fillna(train["price"].median())
    return model, {
        "mae": float(mean_absolute_error(test["price"], pred)),
        "baseline_mae": float(mean_absolute_error(test["price"], baseline_pred)),
        "train_rows": int(len(train)),
        "test_rows": int(len(test)),
    }


def flight_curve(frame: pd.DataFrame, model: Pipeline, route_id: str,
                 departure_month: int, departure_weekday: int) -> pd.DataFrame:
    leads = [7, 14, 21, 30, 45, 60, 75, 90]
    query = pd.DataFrame({
        "route_id": route_id,
        "days_to_departure": leads,
        "departure_month": departure_month,
        "departure_weekday": departure_weekday,
    })
    query["predicted_price"] = model.predict(query)
    query["days_to_departure"] = leads
    return query


def load_hotels() -> tuple[pd.DataFrame, pd.DataFrame]:
    hotels = pd.read_csv(DATA_DIR / "synthetic_hotels.csv")
    prices = pd.read_csv(DATA_DIR / "synthetic_hotel_prices.csv")
    prices["observed_at"] = pd.to_datetime(prices["observed_at"], utc=True)
    prices["check_in"] = pd.to_datetime(prices["check_in"])
    prices["observation_day"] = prices["observed_at"].dt.date
    prices["checkin_month"] = prices["check_in"].dt.month
    prices["checkin_weekday"] = prices["check_in"].dt.dayofweek
    prices["days_to_checkin"] = pd.to_numeric(prices["days_to_checkin"])
    prices["nights"] = pd.to_numeric(prices["nights"])
    prices["adults"] = pd.to_numeric(prices["adults"])
    prices["price_per_night"] = pd.to_numeric(prices["price_per_night"], errors="coerce")
    prices["hotel_id"] = prices["hotel_id"].astype(str)
    hotels["hotel_id"] = hotels["hotel_id"].astype(str)
    frame = prices.merge(hotels, on="hotel_id", how="inner")
    frame = frame[frame["available"].astype(str).str.lower().isin(["true", "1"])]
    frame = frame.dropna(subset=["price_per_night"])
    return frame, hotels


def train_hotel_model(frame: pd.DataFrame) -> tuple[Pipeline, dict[str, float]]:
    cats = ["hotel_id", "city"]
    nums = ["stars", "days_to_checkin", "nights", "adults", "checkin_month", "checkin_weekday"]
    train, test = _temporal_split(frame, "observation_day")
    model = _pipeline(cats, nums)
    model.fit(train[cats + nums], train["price_per_night"])
    pred = model.predict(test[cats + nums])
    baseline = train.groupby("city")["price_per_night"].median()
    baseline_pred = test["city"].map(baseline).fillna(train["price_per_night"].median())
    return model, {
        "mae": float(mean_absolute_error(test["price_per_night"], pred)),
        "baseline_mae": float(mean_absolute_error(test["price_per_night"], baseline_pred)),
        "train_rows": int(len(train)),
        "test_rows": int(len(test)),
    }


def hotel_curve(frame: pd.DataFrame, model: Pipeline, hotel: pd.Series,
                checkin_month: int, checkin_weekday: int, nights: int,
                adults: int) -> pd.DataFrame:
    leads = [7, 14, 21, 30, 45, 60, 75, 90]
    query = pd.DataFrame({
        "hotel_id": str(hotel["hotel_id"]), "city": hotel["city"],
        "stars": float(hotel["stars"]), "days_to_checkin": leads,
        "nights": nights, "adults": adults,
        "checkin_month": checkin_month, "checkin_weekday": checkin_weekday,
    })
    query["predicted_nightly"] = model.predict(query)
    query["predicted_stay_total"] = query["predicted_nightly"] * nights
    return query
