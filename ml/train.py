from __future__ import annotations

import asyncio
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import asyncpg
import joblib
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.ensemble import RandomForestRegressor
from sklearn.metrics import mean_absolute_error
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder

from ml.backtest import (
    evaluate_and_select_promoted_routes,
    make_classifier,
    make_quantile_regressor,
)
from ml.features import (
    DECISION_CATEGORICAL_FEATURES,
    DECISION_NUMERIC_FEATURES,
    build_forward_targets,
    engineer_flight_decision_features,
    load_synthetic_flights,
    load_synthetic_hotels,
)

DATA_DIR = Path(
    os.getenv(
        "SMARTFARE_DATA_DIR",
        "/app/data" if Path("/app/data").exists() else Path(__file__).resolve().parent.parent / "data",
    )
)
ARTIFACTS_DIR = Path(
    os.getenv(
        "SMARTFARE_ARTIFACTS_DIR",
        "/app/artifacts" if Path("/app/artifacts").exists() else Path(__file__).resolve().parent.parent / "artifacts",
    )
)


def _rf_pipeline(categorical: list[str], numeric: list[str]) -> Pipeline:
    prep = ColumnTransformer(
        [
            ("categories", OneHotEncoder(handle_unknown="ignore"), categorical),
            ("numbers", "passthrough", numeric),
        ]
    )
    return Pipeline(
        [
            ("features", prep),
            (
                "model",
                RandomForestRegressor(
                    n_estimators=50,
                    max_depth=16,
                    min_samples_leaf=5,
                    random_state=42,
                    n_jobs=-1,
                ),
            ),
        ]
    )


def _chronological_split(frame: pd.DataFrame, date_column: str) -> tuple[pd.DataFrame, pd.DataFrame]:
    dates = sorted(frame[date_column].dropna().unique())
    if len(dates) < 2:
        raise ValueError("At least two observation dates are required for chronological holdout.")
    first_test = dates[max(1, int(len(dates) * 0.8))]
    return frame[frame[date_column] < first_test], frame[frame[date_column] >= first_test]


async def _fetch_warehouse_flight_features() -> pd.DataFrame:
    database_url = os.getenv("DATABASE_URL", "")
    if not database_url:
        return pd.DataFrame()
    database_url = database_url.replace("postgresql+asyncpg://", "postgresql://", 1)
    include_demo = os.getenv("SMARTFARE_INCLUDE_DEMO_ML", "false").strip().lower() == "true"

    query = """
        select
            route_id,
            observed_date,
            departure_date,
            coalesce(min_total_price_usd, min_total_price)::float as price,
            days_to_departure,
            price_velocity_3d::float as price_velocity_3d,
            price_velocity_7d::float as price_velocity_7d,
            z_score_vs_route_horizon::float as z_score_vs_route_horizon,
            markup_over_historical_min::float as markup_over_historical_min,
            drawdown_from_peak::float as drawdown_from_peak,
            all_data_is_demo
        from analytics_marts.ml_features_flight_pricing
        where ($1::boolean or not all_data_is_demo)
        order by route_id, departure_date, observed_date
    """
    try:
        conn = await asyncpg.connect(database_url)
        try:
            records = await conn.fetch(query, include_demo)
        finally:
            await conn.close()
    except Exception:
        return pd.DataFrame()

    if not records:
        return pd.DataFrame()
    return pd.DataFrame([dict(r) for r in records])


def _train_decision_bundle(
    df: pd.DataFrame,
    *,
    source_name: str,
    is_synthetic: bool,
    horizon_days: int = 14,
) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    if df.empty:
        return None, {
            "source": source_name,
            "is_synthetic": is_synthetic,
            "status": "no_data",
            "observation_days": 0,
            "promoted_routes": [],
            "route_scorecards": {},
            "global_metrics": {},
        }

    engineered = engineer_flight_decision_features(df)
    obs_days = int(engineered["observed_date"].nunique()) if not engineered.empty else 0
    labeled = build_forward_targets(engineered, horizon_days=horizon_days)
    eval_report = evaluate_and_select_promoted_routes(labeled, horizon_days=horizon_days)
    eval_report["source"] = source_name
    eval_report["is_synthetic"] = is_synthetic
    eval_report["observation_days"] = obs_days

    trainable = labeled.dropna(subset=["y_will_drop", "y_min_future_price"])
    if len(trainable) < 30 or trainable["y_will_drop"].nunique() < 2:
        return None, eval_report

    feature_cols = DECISION_CATEGORICAL_FEATURES + DECISION_NUMERIC_FEATURES
    clf = make_classifier()
    clf.fit(trainable[feature_cols], trainable["y_will_drop"].astype(int))

    reg_p10 = make_quantile_regressor(0.10)
    reg_p50 = make_quantile_regressor(0.50)
    reg_p90 = make_quantile_regressor(0.90)
    reg_p10.fit(trainable[feature_cols], trainable["y_min_future_price"])
    reg_p50.fit(trainable[feature_cols], trainable["y_min_future_price"])
    reg_p90.fit(trainable[feature_cols], trainable["y_min_future_price"])

    route_means = engineered.groupby("route_id")["price"].mean().to_dict()
    bundle = {
        "classifier": clf,
        "quantiles": {"p10": reg_p10, "p50": reg_p50, "p90": reg_p90},
        "route_means": {str(k): float(v) for k, v in route_means.items()},
        "promoted_routes": eval_report.get("promoted_routes", []),
        "source": source_name,
        "is_synthetic": is_synthetic,
        "horizon_days": horizon_days,
        "trained_at": datetime.now(timezone.utc).isoformat(),
    }
    return bundle, eval_report


def _train_synthetic_flight_curve(frame: pd.DataFrame, source_file: str) -> tuple[dict[str, Any], dict[str, Any]]:
    cats = ["route_id"]
    nums = ["days_to_departure", "departure_month", "departure_weekday"]
    train, test = _chronological_split(frame, "observed_date")
    model = _rf_pipeline(cats, nums)
    model.fit(train[cats + nums], train["price"])
    pred = model.predict(test[cats + nums])
    baseline = train.groupby("route_id")["price"].median()
    baseline_pred = test["route_id"].map(baseline).fillna(train["price"].median())

    currency_by_route = (
        frame.groupby("route_id")["currency"]
        .agg(lambda s: str(s.mode().iloc[0]) if not s.mode().empty else "USD")
        .to_dict()
    )
    metrics = {
        "source_file": source_file,
        "mae": round(float(mean_absolute_error(test["price"], pred)), 2),
        "baseline_mae": round(float(mean_absolute_error(test["price"], baseline_pred)), 2),
        "train_rows": int(len(train)),
        "test_rows": int(len(test)),
    }
    bundle = {
        "model": model,
        "routes": sorted(frame["route_id"].unique().tolist()),
        "currency_by_route": currency_by_route,
        "metrics": metrics,
        "is_synthetic": True,
        "trained_at": datetime.now(timezone.utc).isoformat(),
    }
    return bundle, metrics


def _train_synthetic_hotel_curve(frame: pd.DataFrame, hotels: pd.DataFrame, source_file: str) -> tuple[dict[str, Any], dict[str, Any]]:
    cats = ["hotel_id", "city"]
    nums = ["stars", "days_to_checkin", "nights", "adults", "checkin_month", "checkin_weekday"]
    train, test = _chronological_split(frame, "observed_date")
    model = _rf_pipeline(cats, nums)
    model.fit(train[cats + nums], train["price_per_night"])
    pred = model.predict(test[cats + nums])
    baseline = train.groupby("city")["price_per_night"].median()
    baseline_pred = test["city"].map(baseline).fillna(train["price_per_night"].median())

    metrics = {
        "source_file": source_file,
        "mae": round(float(mean_absolute_error(test["price_per_night"], pred)), 2),
        "baseline_mae": round(float(mean_absolute_error(test["price_per_night"], baseline_pred)), 2),
        "train_rows": int(len(train)),
        "test_rows": int(len(test)),
    }
    hotel_catalog = [
        {
            "hotel_id": str(row["hotel_id"]),
            "name": str(row["name"]),
            "city": str(row["city"]),
            "stars": float(row["stars"]),
        }
        for _, row in hotels.iterrows()
    ]
    bundle = {
        "model": model,
        "hotels": hotel_catalog,
        "metrics": metrics,
        "is_synthetic": True,
        "trained_at": datetime.now(timezone.utc).isoformat(),
    }
    return bundle, metrics


async def run_training_pipeline() -> dict[str, Any]:
    ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
    trained_at = datetime.now(timezone.utc).isoformat()
    version = datetime.now(timezone.utc).strftime("v%Y%m%d_%H%M%S")

    warehouse_df = await _fetch_warehouse_flight_features()
    warehouse_bundle, warehouse_report = _train_decision_bundle(
        warehouse_df,
        source_name="warehouse_ml_features_flight_pricing",
        is_synthetic=False,
    )
    if warehouse_bundle is not None:
        joblib.dump(warehouse_bundle, ARTIFACTS_DIR / "flight_decision_live.joblib")

    synthetic_decision_report: dict[str, Any] = {}
    flight_curve_metrics: dict[str, Any] = {}
    hotel_curve_metrics: dict[str, Any] = {}

    if (DATA_DIR / "historical_flights_3yrs.csv").exists() or (DATA_DIR / "synthetic_flights_calibrated.csv").exists():
        synth_flights, flight_source_file = load_synthetic_flights(DATA_DIR)
        synth_decision_bundle, synthetic_decision_report = _train_decision_bundle(
            synth_flights,
            source_name=flight_source_file,
            is_synthetic=True,
        )
        if synth_decision_bundle is not None:
            joblib.dump(synth_decision_bundle, ARTIFACTS_DIR / "flight_decision_synthetic.joblib")

        flight_curve_bundle, flight_curve_metrics = _train_synthetic_flight_curve(synth_flights, flight_source_file)
        joblib.dump(flight_curve_bundle, ARTIFACTS_DIR / "synthetic_flight_curve.joblib")

    if (DATA_DIR / "historical_hotels_3yrs.csv").exists() or (
        (DATA_DIR / "synthetic_hotels.csv").exists() and (DATA_DIR / "synthetic_hotel_prices.csv").exists()
    ):
        hotel_frame, hotels_catalog, hotel_source_file = load_synthetic_hotels(DATA_DIR)
        hotel_curve_bundle, hotel_curve_metrics = _train_synthetic_hotel_curve(
            hotel_frame, hotels_catalog, hotel_source_file
        )
        joblib.dump(hotel_curve_bundle, ARTIFACTS_DIR / "synthetic_hotel_curve.joblib")

    scorecard = {
        "model_version": version,
        "trained_at": trained_at,
        "live_decision_model": warehouse_report,
        "synthetic_decision_prototype": synthetic_decision_report,
        "synthetic_flight_curve": flight_curve_metrics,
        "synthetic_hotel_curve": hotel_curve_metrics,
    }
    (ARTIFACTS_DIR / "scorecard.json").write_text(json.dumps(scorecard, indent=2), encoding="utf-8")
    return scorecard


async def main() -> None:
    scorecard = await run_training_pipeline()
    print(json.dumps(scorecard, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
