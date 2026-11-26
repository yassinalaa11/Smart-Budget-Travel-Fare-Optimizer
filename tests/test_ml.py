from __future__ import annotations

from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

from ml.backtest import (
    evaluate_and_select_promoted_routes,
    expanding_window_split,
    simulated_traveler_regret,
)
from ml.features import (
    DECISION_NUMERIC_FEATURES,
    build_forward_targets,
    engineer_flight_decision_features,
)
from ml.registry import ModelRegistry


def test_forward_targets_detect_7pct_drop_within_14_days() -> None:
    df = pd.DataFrame(
        [
            {"route_id": "CAI-DXB", "observed_date": date(2026, 1, 1), "departure_date": date(2026, 2, 15), "price": 200.0},
            {"route_id": "CAI-DXB", "observed_date": date(2026, 1, 6), "departure_date": date(2026, 2, 15), "price": 180.0},
            {"route_id": "CAI-DXB", "observed_date": date(2026, 1, 10), "departure_date": date(2026, 2, 15), "price": 195.0},
            {"route_id": "CAI-DXB", "observed_date": date(2026, 1, 22), "departure_date": date(2026, 2, 15), "price": 190.0},
        ]
    )
    engineered = engineer_flight_decision_features(df)
    for col in DECISION_NUMERIC_FEATURES:
        assert col in engineered.columns
        assert engineered[col].isna().sum() == 0

    labeled = build_forward_targets(engineered, horizon_days=14, drop_threshold=0.07)
    assert labeled.loc[0, "y_min_future_price"] == 180.0
    assert labeled.loc[0, "y_will_drop"] == 1.0
    assert labeled.loc[1, "y_min_future_price"] == 195.0
    assert labeled.loc[1, "y_will_drop"] == 0.0
    assert labeled.loc[2, "y_min_future_price"] == 190.0
    assert labeled.loc[2, "y_will_drop"] == 0.0
    assert pd.isna(labeled.loc[3, "y_will_drop"])


def test_expanding_window_split_enforces_horizon_leakage_gap(sample_flight_history_df: pd.DataFrame) -> None:
    engineered = engineer_flight_decision_features(sample_flight_history_df)
    labeled = build_forward_targets(engineered, horizon_days=14)
    train, val = expanding_window_split(labeled, horizon_days=14, train_fraction=0.75)

    assert not train.empty
    assert not val.empty
    gap_days = (val["observed_date"].min() - train["observed_date"].max()).days
    assert gap_days > 14


def test_simulated_traveler_regret_zero_when_optimal() -> None:
    current = np.array([250.0, 180.0])
    future_min = np.array([200.0, 210.0])
    optimal_wait = np.array([True, False])
    worst_wait = np.array([False, True])

    assert simulated_traveler_regret(current, future_min, optimal_wait) == 0.0
    assert simulated_traveler_regret(current, future_min, worst_wait) == 40.0


def test_backtest_evaluates_and_registry_predicts_ordered_quantiles(
    sample_flight_history_df: pd.DataFrame,
    trained_artifacts_dir: Path,
) -> None:
    engineered = engineer_flight_decision_features(sample_flight_history_df)
    labeled = build_forward_targets(engineered, horizon_days=14)
    report = evaluate_and_select_promoted_routes(labeled, horizon_days=14)

    assert report["status"] == "evaluated"
    assert report["train_rows"] > 0
    assert report["val_rows"] > 0
    assert "model_brier" in report["global_metrics"]
    assert "model_traveler_regret" in report["global_metrics"]

    registry = ModelRegistry(artifacts_dir=trained_artifacts_dir)
    status = registry.status()
    assert status["artifacts_loaded"] is True
    assert status["model_version"] == "v_pytest_001"
    assert registry.is_live_route_promoted("CAI-DXB") is False

    pred = registry.predict_decision(
        route_id="CAI-DXB",
        observed_date=date(2026, 2, 1),
        departure_date=date(2026, 3, 20),
        current_price=245.0,
        allow_synthetic_prototype=True,
    )
    assert pred is not None
    assert pred["model_stage"] == "synthetic_ml_prototype"
    assert 0.0 <= pred["drop_probability"] <= 1.0
    assert pred["expected_min_price_p10"] <= pred["expected_min_price_p50"] <= pred["expected_min_price_p90"]
