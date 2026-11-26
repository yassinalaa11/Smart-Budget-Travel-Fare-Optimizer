from __future__ import annotations

import json
from contextlib import asynccontextmanager
from datetime import date, timedelta
from pathlib import Path
from typing import Any, AsyncIterator

import joblib
import numpy as np
import pandas as pd
import pytest

from ml.train import _train_decision_bundle


class FakeConnection:
    def __init__(
        self,
        *,
        fetchrow_results: list[dict[str, Any] | None] | None = None,
        fetch_results: list[list[dict[str, Any]]] | None = None,
        fetchval_results: list[Any] | None = None,
    ) -> None:
        self._fetchrow_results = list(fetchrow_results or [])
        self._fetch_results = list(fetch_results or [])
        self._fetchval_results = list(fetchval_results or [])
        self.executed: list[tuple[str, tuple[Any, ...]]] = []

    async def execute(self, query: str, *args: Any) -> str:
        self.executed.append((query, args))
        return "OK"

    async def fetchrow(self, query: str, *args: Any) -> dict[str, Any] | None:
        self.executed.append((query, args))
        if self._fetchrow_results:
            return self._fetchrow_results.pop(0)
        return None

    async def fetch(self, query: str, *args: Any) -> list[dict[str, Any]]:
        self.executed.append((query, args))
        if self._fetch_results:
            return self._fetch_results.pop(0)
        return []

    async def fetchval(self, query: str, *args: Any) -> Any:
        self.executed.append((query, args))
        if self._fetchval_results:
            return self._fetchval_results.pop(0)
        return None


class FakePool:
    def __init__(self, conn: FakeConnection) -> None:
        self.conn = conn
        self.closed = False

    @asynccontextmanager
    async def acquire(self) -> AsyncIterator[FakeConnection]:
        yield self.conn

    async def close(self) -> None:
        self.closed = True


@pytest.fixture
def sample_flight_history_df() -> pd.DataFrame:
    rng = np.random.default_rng(42)
    rows: list[dict[str, Any]] = []
    start_obs = date(2026, 1, 1)
    for route_id, base in [("CAI-DXB", 220.0), ("CAI-IST", 260.0)]:
        for dep_offset in (60, 75, 90, 105):
            dep_date = start_obs + timedelta(days=dep_offset)
            for day in range(55):
                obs_date = start_obs + timedelta(days=day)
                if obs_date >= dep_date:
                    continue
                lead = (dep_date - obs_date).days
                cliff = 75.0 if lead <= 14 else 0.0
                promo_bump = 32.0 if (day % 11) < 3 else 0.0
                price = max(80.0, base + cliff + promo_bump + float(rng.normal(0.0, 10.0)))
                rows.append(
                    {
                        "route_id": route_id,
                        "observed_date": obs_date,
                        "departure_date": dep_date,
                        "price": round(price, 2),
                    }
                )
    return pd.DataFrame(rows)


@pytest.fixture
def trained_artifacts_dir(tmp_path: Path, sample_flight_history_df: pd.DataFrame) -> Path:
    bundle, report = _train_decision_bundle(
        sample_flight_history_df,
        source_name="pytest_synthetic",
        is_synthetic=True,
        horizon_days=14,
    )
    assert bundle is not None
    bundle["promoted_routes"] = ["CAI-DXB"]
    joblib.dump(bundle, tmp_path / "flight_decision_synthetic.joblib")
    scorecard = {
        "model_version": "v_pytest_001",
        "trained_at": "2026-11-26T00:00:00+00:00",
        "live_decision_model": {
            "status": "cold_start_insufficient_history",
            "promoted_routes": [],
            "observation_days": 1,
        },
        "synthetic_decision_prototype": report,
    }
    (tmp_path / "scorecard.json").write_text(json.dumps(scorecard), encoding="utf-8")
    return tmp_path
