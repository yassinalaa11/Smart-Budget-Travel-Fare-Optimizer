from __future__ import annotations

from datetime import timedelta
from typing import Any

import numpy as np
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.ensemble import HistGradientBoostingClassifier, HistGradientBoostingRegressor
from sklearn.metrics import brier_score_loss, mean_absolute_error, precision_score, roc_auc_score
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder

from ml.features import DECISION_CATEGORICAL_FEATURES, DECISION_NUMERIC_FEATURES


def make_preprocessor() -> ColumnTransformer:
    return ColumnTransformer(
        [
            (
                "cat",
                OneHotEncoder(handle_unknown="ignore", sparse_output=False),
                DECISION_CATEGORICAL_FEATURES,
            ),
            ("num", "passthrough", DECISION_NUMERIC_FEATURES),
        ]
    )


def make_classifier() -> Pipeline:
    return Pipeline(
        [
            ("prep", make_preprocessor()),
            (
                "model",
                HistGradientBoostingClassifier(
                    max_iter=120,
                    max_depth=6,
                    learning_rate=0.06,
                    min_samples_leaf=10,
                    random_state=42,
                ),
            ),
        ]
    )


def make_quantile_regressor(quantile: float) -> Pipeline:
    return Pipeline(
        [
            ("prep", make_preprocessor()),
            (
                "model",
                HistGradientBoostingRegressor(
                    loss="quantile",
                    quantile=quantile,
                    max_iter=120,
                    max_depth=6,
                    learning_rate=0.06,
                    min_samples_leaf=10,
                    random_state=42,
                ),
            ),
        ]
    )


def expanding_window_split(
    df: pd.DataFrame,
    *,
    horizon_days: int = 14,
    train_fraction: float = 0.75,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    labeled = df.dropna(subset=["y_will_drop", "y_min_future_price"]).copy()
    dates = sorted(labeled["observed_date"].unique())
    if len(dates) < 3:
        return pd.DataFrame(), pd.DataFrame()

    split_idx = max(1, min(len(dates) - 1, int(len(dates) * train_fraction)))
    val_start = pd.Timestamp(dates[split_idx])
    train_cutoff = val_start - timedelta(days=horizon_days)

    train = labeled[labeled["observed_date"] < train_cutoff].copy()
    val = labeled[labeled["observed_date"] >= val_start].copy()
    return train, val


def simulated_traveler_regret(
    current_prices: np.ndarray,
    future_min_prices: np.ndarray,
    wait_mask: np.ndarray,
) -> float:
    optimal = np.minimum(current_prices, future_min_prices)
    paid = np.where(wait_mask, future_min_prices, current_prices)
    regret = np.maximum(paid - optimal, 0.0)
    return float(np.mean(regret)) if len(regret) else 0.0


def evaluate_and_select_promoted_routes(
    df: pd.DataFrame,
    *,
    horizon_days: int = 14,
    min_route_obs_days: int = 14,
    min_route_val_rows: int = 15,
    promotion_gain_threshold: float = 0.05,
) -> dict[str, Any]:
    train, val = expanding_window_split(df, horizon_days=horizon_days)
    if train.empty or val.empty or len(train) < 30 or len(val) < 10:
        return {
            "status": "cold_start_insufficient_history",
            "train_rows": int(len(train)),
            "val_rows": int(len(val)),
            "promoted_routes": [],
            "route_scorecards": {},
            "global_metrics": {},
        }

    feature_cols = DECISION_CATEGORICAL_FEATURES + DECISION_NUMERIC_FEATURES
    y_train_cls = train["y_will_drop"].astype(int)
    y_val_cls = val["y_will_drop"].astype(int)

    if y_train_cls.nunique() < 2:
        return {
            "status": "single_class_target",
            "train_rows": int(len(train)),
            "val_rows": int(len(val)),
            "promoted_routes": [],
            "route_scorecards": {},
            "global_metrics": {},
        }

    clf = make_classifier()
    clf.fit(train[feature_cols], y_train_cls)
    val_probs = clf.predict_proba(val[feature_cols])[:, 1]

    reg_p10 = make_quantile_regressor(0.10)
    reg_p50 = make_quantile_regressor(0.50)
    reg_p90 = make_quantile_regressor(0.90)
    reg_p10.fit(train[feature_cols], train["y_min_future_price"])
    reg_p50.fit(train[feature_cols], train["y_min_future_price"])
    reg_p90.fit(train[feature_cols], train["y_min_future_price"])

    pred_p50 = reg_p50.predict(val[feature_cols])

    base_rate = float(y_train_cls.mean())
    baseline_probs = np.full(len(val), base_rate, dtype=float)
    route_p80 = train.groupby("route_id")["price"].quantile(0.80)
    val_p80 = val["route_id"].map(route_p80).fillna(float(train["price"].quantile(0.80))).to_numpy()
    baseline_wait_mask = (val["price"].to_numpy() >= val_p80) & (val["days_to_departure"].to_numpy() > 21)
    model_wait_mask = (val_probs >= 0.50) & (val["days_to_departure"].to_numpy() > 14)

    current_prices = val["price"].to_numpy(dtype=float)
    future_mins = val["y_min_future_price"].to_numpy(dtype=float)

    model_brier = float(brier_score_loss(y_val_cls, val_probs))
    baseline_brier = float(brier_score_loss(y_val_cls, baseline_probs))
    model_mae = float(mean_absolute_error(future_mins, pred_p50))
    baseline_mae = float(mean_absolute_error(future_mins, current_prices))
    model_regret = simulated_traveler_regret(current_prices, future_mins, model_wait_mask)
    baseline_regret = simulated_traveler_regret(current_prices, future_mins, baseline_wait_mask)
    roc_auc = float(roc_auc_score(y_val_cls, val_probs)) if y_val_cls.nunique() == 2 else None
    precision = float(precision_score(y_val_cls, (val_probs >= 0.50).astype(int), zero_division=0))

    val_eval = val[["route_id", "observed_date", "price", "y_min_future_price", "y_will_drop"]].copy()
    val_eval["prob"] = val_probs
    val_eval["pred_p50"] = pred_p50
    val_eval["model_wait"] = model_wait_mask
    val_eval["base_wait"] = baseline_wait_mask

    obs_days_by_route = df.groupby("route_id")["observed_date"].nunique().to_dict()
    promoted_routes: list[str] = []
    route_scorecards: dict[str, dict[str, Any]] = {}

    for route_id, rgrp in val_eval.groupby("route_id"):
        obs_days = int(obs_days_by_route.get(route_id, 0))
        r_y = rgrp["y_will_drop"].astype(int).to_numpy()
        r_prob = rgrp["prob"].to_numpy()
        r_brier = float(brier_score_loss(r_y, r_prob))
        r_base_brier = float(brier_score_loss(r_y, np.full(len(r_y), base_rate)))
        r_regret = simulated_traveler_regret(
            rgrp["price"].to_numpy(dtype=float),
            rgrp["y_min_future_price"].to_numpy(dtype=float),
            rgrp["model_wait"].to_numpy(dtype=bool),
        )
        r_base_regret = simulated_traveler_regret(
            rgrp["price"].to_numpy(dtype=float),
            rgrp["y_min_future_price"].to_numpy(dtype=float),
            rgrp["base_wait"].to_numpy(dtype=bool),
        )
        brier_gain = (r_base_brier - r_brier) / r_base_brier if r_base_brier > 0 else 0.0
        regret_gain = (r_base_regret - r_regret) / r_base_regret if r_base_regret > 0 else 0.0

        promoted = (
            obs_days >= min_route_obs_days
            and len(rgrp) >= min_route_val_rows
            and (brier_gain >= promotion_gain_threshold or regret_gain >= promotion_gain_threshold)
        )
        if promoted:
            promoted_routes.append(str(route_id))

        route_scorecards[str(route_id)] = {
            "observation_days": obs_days,
            "val_rows": int(len(rgrp)),
            "model_brier": round(r_brier, 4),
            "baseline_brier": round(r_base_brier, 4),
            "brier_gain_pct": round(brier_gain * 100.0, 2),
            "model_regret": round(r_regret, 2),
            "baseline_regret": round(r_base_regret, 2),
            "regret_gain_pct": round(regret_gain * 100.0, 2),
            "promoted": bool(promoted),
        }

    return {
        "status": "evaluated",
        "train_rows": int(len(train)),
        "val_rows": int(len(val)),
        "promoted_routes": sorted(promoted_routes),
        "route_scorecards": route_scorecards,
        "global_metrics": {
            "model_brier": round(model_brier, 4),
            "baseline_brier": round(baseline_brier, 4),
            "roc_auc": round(roc_auc, 4) if roc_auc is not None else None,
            "precision_at_50": round(precision, 4),
            "quantile_p50_mae": round(model_mae, 2),
            "baseline_mae": round(baseline_mae, 2),
            "model_traveler_regret": round(model_regret, 2),
            "baseline_traveler_regret": round(baseline_regret, 2),
        },
    }
