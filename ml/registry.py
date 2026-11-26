from __future__ import annotations

import json
import os
from datetime import date
from pathlib import Path
from typing import Any

import joblib
import pandas as pd

from ml.features import build_single_decision_row

DEFAULT_LEADS = [7, 14, 21, 30, 45, 60, 75, 90]


class ModelRegistry:
    def __init__(self, artifacts_dir: Path | None = None) -> None:
        self.artifacts_dir = artifacts_dir or Path(
            os.getenv(
                "SMARTFARE_ARTIFACTS_DIR",
                "/app/artifacts" if Path("/app/artifacts").exists() else Path(__file__).resolve().parent.parent / "artifacts",
            )
        )
        self.scorecard: dict[str, Any] = {}
        self.live_decision: dict[str, Any] | None = None
        self.synthetic_decision: dict[str, Any] | None = None
        self.flight_curve_bundle: dict[str, Any] | None = None
        self.hotel_curve_bundle: dict[str, Any] | None = None
        self.reload()

    def reload(self) -> dict[str, Any]:
        scorecard_path = self.artifacts_dir / "scorecard.json"
        self.scorecard = (
            json.loads(scorecard_path.read_text(encoding="utf-8"))
            if scorecard_path.exists()
            else {}
        )
        self.live_decision = self._load_artifact("flight_decision_live.joblib")
        self.synthetic_decision = self._load_artifact("flight_decision_synthetic.joblib")
        self.flight_curve_bundle = self._load_artifact("synthetic_flight_curve.joblib")
        self.hotel_curve_bundle = self._load_artifact("synthetic_hotel_curve.joblib")
        return self.status()

    def _load_artifact(self, filename: str) -> dict[str, Any] | None:
        path = self.artifacts_dir / filename
        if not path.exists():
            return None
        return joblib.load(path)

    def status(self) -> dict[str, Any]:
        live_report = self.scorecard.get("live_decision_model") or {}
        synth_report = self.scorecard.get("synthetic_decision_prototype") or {}
        return {
            "artifacts_loaded": bool(
                self.flight_curve_bundle
                or self.hotel_curve_bundle
                or self.live_decision
                or self.synthetic_decision
            ),
            "model_version": self.scorecard.get("model_version"),
            "trained_at": self.scorecard.get("trained_at"),
            "live_model_status": live_report.get("status", "not_trained"),
            "live_promoted_routes": live_report.get("promoted_routes", []),
            "synthetic_decision_status": synth_report.get("status", "not_trained"),
            "synthetic_promoted_routes": synth_report.get("promoted_routes", []),
            "scorecard": self.scorecard,
        }

    def is_live_route_promoted(self, route_id: str) -> bool:
        if not self.live_decision:
            return False
        return route_id.upper() in set(self.live_decision.get("promoted_routes") or [])

    def predict_decision(
        self,
        *,
        route_id: str,
        observed_date: date,
        departure_date: date,
        current_price: float,
        price_velocity_3d: float | None = None,
        price_velocity_7d: float | None = None,
        z_score_vs_route_horizon: float | None = None,
        markup_over_historical_min: float | None = None,
        drawdown_from_peak: float | None = None,
        allow_synthetic_prototype: bool = False,
    ) -> dict[str, Any] | None:
        normalized_route = route_id.upper()
        bundle: dict[str, Any] | None = None
        stage = "phase_1_statistical_baseline"

        if self.live_decision and self.is_live_route_promoted(normalized_route):
            bundle = self.live_decision
            stage = "phase_2_ml_promoted"
        elif allow_synthetic_prototype and self.synthetic_decision:
            bundle = self.synthetic_decision
            stage = "synthetic_ml_prototype"
        else:
            return None

        route_mean = float((bundle.get("route_means") or {}).get(normalized_route, current_price))
        row = build_single_decision_row(
            route_id=normalized_route,
            observed_date=pd.Timestamp(observed_date),
            departure_date=pd.Timestamp(departure_date),
            price=current_price,
            route_mean_price=route_mean,
            price_velocity_3d=price_velocity_3d,
            price_velocity_7d=price_velocity_7d,
            z_score_vs_route_horizon=z_score_vs_route_horizon,
            markup_over_historical_min=markup_over_historical_min,
            drawdown_from_peak=drawdown_from_peak,
        )

        prob_drop = float(bundle["classifier"].predict_proba(row)[0, 1])
        q10 = float(bundle["quantiles"]["p10"].predict(row)[0])
        q50 = float(bundle["quantiles"]["p50"].predict(row)[0])
        q90 = float(bundle["quantiles"]["p90"].predict(row)[0])
        ordered = sorted([max(q10, 1.0), max(q50, 1.0), max(q90, 1.0)])

        return {
            "model_stage": stage,
            "is_synthetic_model": bool(bundle.get("is_synthetic", False)),
            "drop_probability": round(prob_drop, 4),
            "expected_min_price_p10": round(ordered[0], 2),
            "expected_min_price_p50": round(ordered[1], 2),
            "expected_min_price_p90": round(ordered[2], 2),
        }

    def flight_curve_options(self) -> dict[str, Any]:
        if not self.flight_curve_bundle:
            return {"routes": [], "currency_by_route": {}}
        return {
            "routes": self.flight_curve_bundle["routes"],
            "currency_by_route": self.flight_curve_bundle["currency_by_route"],
        }

    def predict_flight_curve(
        self,
        *,
        route_id: str,
        departure_month: int,
        departure_weekday: int,
    ) -> dict[str, Any]:
        if not self.flight_curve_bundle:
            raise FileNotFoundError("Flight prediction artifact not trained yet.")
        normalized = route_id.upper()
        query = pd.DataFrame(
            {
                "route_id": normalized,
                "days_to_departure": DEFAULT_LEADS,
                "departure_month": departure_month,
                "departure_weekday": departure_weekday,
            }
        )
        preds = self.flight_curve_bundle["model"].predict(query)
        points = [
            {"days_to_departure": int(lead), "predicted_price": round(float(val), 2)}
            for lead, val in zip(DEFAULT_LEADS, preds)
        ]
        best = min(points, key=lambda p: p["predicted_price"])
        currency = self.flight_curve_bundle["currency_by_route"].get(normalized, "USD")
        return {
            "route_id": normalized,
            "currency": currency,
            "is_synthetic": True,
            "best_days_to_departure": best["days_to_departure"],
            "lowest_predicted_price": best["predicted_price"],
            "metrics": self.flight_curve_bundle["metrics"],
            "curve": points,
        }

    def hotel_curve_options(self) -> list[dict[str, Any]]:
        if not self.hotel_curve_bundle:
            return []
        return self.hotel_curve_bundle["hotels"]

    def predict_hotel_curve(
        self,
        *,
        hotel_id: str,
        checkin_month: int,
        checkin_weekday: int,
        nights: int,
        adults: int,
    ) -> dict[str, Any]:
        if not self.hotel_curve_bundle:
            raise FileNotFoundError("Hotel prediction artifact not trained yet.")
        hotel_lookup = {h["hotel_id"]: h for h in self.hotel_curve_bundle["hotels"]}
        hotel = hotel_lookup.get(str(hotel_id))
        if not hotel:
            raise KeyError(f"Unknown hotel_id: {hotel_id}")

        query = pd.DataFrame(
            {
                "hotel_id": str(hotel["hotel_id"]),
                "city": hotel["city"],
                "stars": float(hotel["stars"]),
                "days_to_checkin": DEFAULT_LEADS,
                "nights": int(nights),
                "adults": int(adults),
                "checkin_month": int(checkin_month),
                "checkin_weekday": int(checkin_weekday),
            }
        )
        preds = self.hotel_curve_bundle["model"].predict(query)
        points = [
            {
                "days_to_checkin": int(lead),
                "predicted_nightly": round(float(val), 2),
                "predicted_stay_total": round(float(val) * int(nights), 2),
            }
            for lead, val in zip(DEFAULT_LEADS, preds)
        ]
        best = min(points, key=lambda p: p["predicted_nightly"])
        return {
            "hotel_id": hotel["hotel_id"],
            "hotel_name": hotel["name"],
            "city": hotel["city"],
            "currency": "USD",
            "nights": int(nights),
            "adults": int(adults),
            "is_synthetic": True,
            "best_days_to_checkin": best["days_to_checkin"],
            "lowest_predicted_nightly": best["predicted_nightly"],
            "lowest_predicted_stay_total": best["predicted_stay_total"],
            "metrics": self.hotel_curve_bundle["metrics"],
            "curve": points,
        }
