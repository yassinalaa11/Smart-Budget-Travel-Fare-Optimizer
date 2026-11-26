#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys

import numpy as np
import pandas as pd

START = pd.Timestamp("2023-11-15")
ROWS_PER_YEAR = 30_000
LEADS = np.array([1, 2, 3, 5, 7, 10, 14, 21, 28, 35, 45, 55, 65, 75, 85, 95, 108, 120])
OBS_KEEP_PROB = 0.88

USD_AIRFARE_INFLATION = 0.04
FX_PASS_THROUGH = 0.85
FX_HALFLIFE_DAYS = 10
LOCAL_COST_WEIGHT = {"egypt_based": 0.25, "foreign": 0.10}
EGYPT_BASED = ["EgyptAir", "Air Cairo", "Nile Air", "Nesma", "Air Arabia Egypt"]

FX_ANCHORS = [
    ("2023-08-15", 30.90), ("2024-03-05", 30.90), ("2024-03-06", 47.00), ("2024-03-12", 48.20),
    ("2024-07-01", 48.00), ("2024-09-15", 48.40), ("2024-11-01", 49.20), ("2024-12-15", 50.60),
    ("2025-03-31", 50.60), ("2025-04-09", 51.80), ("2025-05-05", 50.50), ("2025-06-15", 50.00),
    ("2025-07-31", 49.50), ("2025-08-31", 48.50), ("2025-10-01", 47.80), ("2025-11-15", 47.50),
    ("2026-02-26", 47.99), ("2026-03-15", 51.00), ("2026-04-15", 52.50), ("2026-05-21", 53.00),
    ("2026-06-25", 49.00), ("2026-08-06", 48.30), ("2026-09-15", 49.50), ("2026-10-11", 52.50),
    ("2026-10-14", 53.05), ("2026-10-23", 55.35), ("2026-10-28", 56.20), ("2026-11-12", 53.40),
    ("2026-11-15", 53.75),
]
CPI_ANCHORS = [
    ("2023-11-15", 0.0), ("2024-03-15", 0.12), ("2024-10-15", 0.235),
    ("2025-10-15", 0.353), ("2026-10-15", 0.4726), ("2026-11-15", 0.4818),
]

GROUP = {
    "LHR": "EUROPE", "CDG": "EUROPE", "FRA": "EUROPE", "FCO": "EUROPE", "ATH": "EUROPE",
    "JED": "HOLY", "RUH": "HOLY", "DXB": "GULF", "DOH": "GULF", "KWI": "GULF",
    "AUH": "GULF", "IST": "REGIONAL", "AMM": "REGIONAL",
    "JFK": "EUROPE", "YYZ": "EUROPE", "BOM": "REGIONAL",
}
MONTH_MULT = {
    "EUROPE":   [.95, .95, .98, 1.02, 1.03, 1.10, 1.28, 1.32, 1.05, .97, .92, 1.00],
    "HOLY":     [1.05, 1.00, 1.02, 1.00, 1.00, .98, .95, .92, .95, 1.00, 1.05, 1.08],
    "GULF":     [1.05, 1.02, 1.00, .98, .95, 1.02, 1.10, 1.12, .98, 1.00, 1.03, 1.08],
    "REGIONAL": [.92, .92, .96, 1.02, 1.05, 1.12, 1.22, 1.25, 1.05, .98, .93, .98],
}
DOW_MULT = [.96, .94, .95, 1.05, 1.09, 1.03, 1.01]
HOLIDAYS = [
    ("ramadan24", "2024-03-11", "2024-04-09", {"HOLY": 1.25}, .96),
    ("fitr24", "2024-04-08", "2024-04-16", {"HOLY": 1.10}, 1.15),
    ("adha24", "2024-06-13", "2024-06-22", {"HOLY": 1.30}, 1.18),
    ("ramadan25", "2025-03-01", "2025-03-29", {"HOLY": 1.25}, .96),
    ("fitr25", "2025-03-28", "2025-04-05", {"HOLY": 1.10}, 1.15),
    ("adha25", "2025-06-03", "2025-06-12", {"HOLY": 1.30}, 1.18),
    ("ramadan26", "2026-02-18", "2026-03-19", {"HOLY": 1.25}, .96),
    ("fitr26", "2026-03-18", "2026-03-26", {"HOLY": 1.10}, 1.15),
    ("adha26", "2026-05-24", "2026-06-02", {"HOLY": 1.30}, 1.18),
    ("ramadan27", "2027-02-08", "2027-03-09", {"HOLY": 1.25}, .96),
    ("fitr27", "2027-03-08", "2027-03-16", {"HOLY": 1.10}, 1.15),
    ("xmas23", "2023-12-20", "2024-01-04", {"EUROPE": 1.30}, 1.22),
    ("xmas24", "2024-12-20", "2025-01-04", {"EUROPE": 1.30}, 1.22),
    ("xmas25", "2025-12-20", "2026-01-04", {"EUROPE": 1.30}, 1.22),
    ("xmas26", "2026-12-20", "2027-01-04", {"EUROPE": 1.30}, 1.22),
    ("easter24", "2024-05-03", "2024-05-08", {}, 1.10),
    ("easter25", "2025-04-18", "2025-04-22", {}, 1.10),
    ("easter26", "2026-04-10", "2026-04-14", {}, 1.10),
    ("easter27", "2027-04-30", "2027-05-04", {}, 1.10),
    ("break24", "2024-01-27", "2024-02-10", {}, 1.07),
    ("break25", "2025-01-25", "2025-02-08", {}, 1.07),
    ("break26", "2026-01-24", "2026-02-07", {}, 1.07),
]

LEAD_KNOT_DAYS = np.array([0, 1, 3, 7, 14, 21, 30, 45, 60, 90, 120, 180])
LEAD_KNOT_VALS = np.array([1.85, 1.70, 1.45, 1.18, 1.00, .89, .82, .80, .80, .84, .88, .92])
CALIBRATE_ROUTE_LEAD_SENSITIVITY = True

NOISE_SD = 0.025
TRIP_INIT_SD = 0.03
TRIP_STEP_SD, TRIP_PHI = 0.02, 0.85
MARKET_SD, MARKET_PHI = 0.008, 0.97
PROMO_START_PROB, PROMO_LEN, PROMO_DEPTH = 0.012, (3, 10), (0.85, 0.93)
PRICE_FLOOR_FRAC = 0.25
PRICE_CAP_MULT = 1.5

ORIGIN_TZ = "Africa/Cairo"
DEST_TZ = {
    "AMM": "Asia/Amman", "ATH": "Europe/Athens", "CDG": "Europe/Paris", "DOH": "Asia/Qatar",
    "DXB": "Asia/Dubai", "AUH": "Asia/Dubai", "FCO": "Europe/Rome", "FRA": "Europe/Berlin",
    "IST": "Europe/Istanbul", "JED": "Asia/Riyadh", "KWI": "Asia/Kuwait", "LHR": "Europe/London",
    "RUH": "Asia/Riyadh", "JFK": "America/New_York", "YYZ": "America/Toronto", "BOM": "Asia/Kolkata",
}
DEST_UTC_OFFSET_HOURS = {
    "AMM": 3, "ATH": 2, "CDG": 1, "DOH": 3, "DXB": 4, "AUH": 4, "FCO": 1, "FRA": 1,
    "IST": 3, "JED": 3, "KWI": 3, "LHR": 0, "RUH": 3, "JFK": -5, "YYZ": -5, "BOM": 5.5,
}
AIRLINE_FIRST_SERVICE: dict[str, str] = {}


def parse_naive_timestamps(series: pd.Series) -> pd.Series:
    parsed = pd.to_datetime(series, utc=True, format="mixed")
    return parsed.dt.tz_localize(None)


def detect_price_column(df: pd.DataFrame) -> tuple[str, str]:
    cands = [
        c for c in df.columns
        if c.lower().startswith("price") and c.lower() != "price_level"
        and pd.api.types.is_numeric_dtype(df[c])
    ]
    if not cands:
        sys.exit("No numeric price column found in the baseline file.")
    col = cands[0]
    if "currency" in df.columns:
        ccy = str(df["currency"].dropna().mode().iat[0]).upper()
    elif "_" in col:
        ccy = col.split("_")[-1].upper()
    else:
        ccy = "USD"
    return col, ccy


def daily_series(anchors, index: pd.DatetimeIndex, log: bool = True) -> np.ndarray:
    xs = np.array([(pd.Timestamp(d) - START).days for d, _ in anchors], float)
    ys = np.array([v for _, v in anchors], float)
    ys = np.log(ys) if log else ys
    out = np.interp((index - START).days.to_numpy(float), xs, ys)
    return np.exp(out) if log else out


def build_macro(t0: pd.Timestamp, end: pd.Timestamp, rng, fx_csv: str | None):
    idx = pd.date_range(START - pd.Timedelta(days=90), t0.normalize(), freq="D")
    if fx_csv:
        raw = pd.read_csv(fx_csv, parse_dates=["date"]).set_index("date")["usd_egp"]
        fx = raw.reindex(idx).ffill().bfill().to_numpy(float)
    else:
        fx = daily_series(FX_ANCHORS, idx)
        post = idx >= pd.Timestamp("2024-03-06")
        fx = fx * np.exp(rng.normal(0, np.where(post, 0.0015, 0.0003)))
    fx_s = pd.Series(fx, index=idx)
    fx_eff = np.exp(np.log(fx_s).ewm(halflife=FX_HALFLIFE_DAYS, adjust=False).mean())
    cpi = pd.Series(np.exp(daily_series(CPI_ANCHORS, idx, log=False)), index=idx)
    return fx_s, fx_eff / fx_eff.loc[t0.normalize()], cpi / cpi.loc[t0.normalize()]


def build_season(dates: pd.DatetimeIndex) -> dict[str, np.ndarray]:
    table = {}
    for g, months in MONTH_MULT.items():
        m = np.array([months[d.month - 1] for d in dates], float)
        for _, s, e, by_group, default in HOLIDAYS:
            sel = (dates >= pd.Timestamp(s)) & (dates <= pd.Timestamp(e))
            m[sel] *= by_group.get(g, default)
        table[g] = pd.Series(m).rolling(5, center=True, min_periods=1).mean().to_numpy()
    return table


def lead_curve(lead: np.ndarray, sens: np.ndarray) -> np.ndarray:
    return np.exp(sens * np.interp(lead, LEAD_KNOT_DAYS, np.log(LEAD_KNOT_VALS)))


def estimate_route_sensitivity(base: pd.DataFrame, price_col: str, season, t0: pd.Timestamp) -> dict:
    out = {r: 1.0 for r in base["destination"].unique()}
    if not CALIBRATE_ROUTE_LEAD_SENSITIVITY:
        return out
    d = base.dropna(subset=[price_col]).copy()
    d["fd"] = pd.to_datetime(d["flight_date"])
    dates = sorted(d["fd"].unique())
    if len(dates) < 2:
        return out
    a, b = dates[0], dates[1]
    la, lb = (a - t0.normalize()).days, (b - t0.normalize()).days
    piv = d.groupby(["destination", "flight_number", "fd"])[price_col].median().unstack()
    base_slope = np.log(
        np.interp(lb, LEAD_KNOT_DAYS, LEAD_KNOT_VALS) / np.interp(la, LEAD_KNOT_DAYS, LEAD_KNOT_VALS)
    )
    if abs(base_slope) < 1e-6:
        return out
    for r, grp in piv.groupby(level=0):
        if a not in grp.columns or b not in grp.columns:
            continue
        ratio = (grp[b] / grp[a]).dropna()
        if len(ratio) < 5:
            continue
        g = GROUP.get(r, "REGIONAL")
        ia, ib = (pd.Timestamp(a) - START).days, (pd.Timestamp(b) - START).days
        c = np.log(
            season[g][ib] * DOW_MULT[pd.Timestamp(b).dayofweek]
            / (season[g][ia] * DOW_MULT[pd.Timestamp(a).dayofweek])
        )
        raw = float(np.clip((np.log(ratio.median()) - c) / base_slope, 0.4, 1.6))
        w = len(ratio) / (len(ratio) + 20)
        out[r] = 1 + w * (raw - 1)
    return out


def compute_arrival(dep: pd.DatetimeIndex, duration: np.ndarray, dest: np.ndarray) -> np.ndarray:
    out = np.empty(len(dep), dtype="datetime64[ns]")
    for code in np.unique(dest):
        pos = np.flatnonzero(dest == code)
        try:
            d = dep[pos].tz_localize(ORIGIN_TZ, ambiguous=np.zeros(len(pos), bool), nonexistent="shift_forward")
            a = (d + pd.to_timedelta(duration[pos], unit="m")).tz_convert(DEST_TZ[code]).tz_localize(None)
            out[pos] = a.to_numpy(dtype="datetime64[ns]")
        except Exception:
            offset_diff = DEST_UTC_OFFSET_HOURS.get(code, 2) - 2
            a = dep[pos] + pd.to_timedelta(duration[pos], unit="m") + pd.to_timedelta(offset_diff, unit="h")
            out[pos] = a.to_numpy(dtype="datetime64[ns]")
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--baseline", default="data/cai_international_flights.csv")
    ap.add_argument("--out", default="data/historical_flights_3yrs.csv")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--rows-per-year", type=int, default=ROWS_PER_YEAR)
    ap.add_argument("--fx-csv", default=None)
    ap.add_argument("--no-fx-columns", action="store_true")
    ap.add_argument("--add-fx-columns", action="store_true")
    ap.add_argument("--no-provenance-col", action="store_true")
    a = ap.parse_args()
    if not 10_000 <= a.rows_per_year <= 50_000:
        sys.exit("--rows-per-year must be between 10,000 and 50,000.")
    rng = np.random.default_rng(a.seed)

    base = pd.read_csv(a.baseline)
    cols, dtypes = list(base.columns), base.dtypes.to_dict()
    price_col, ccy = detect_price_column(base)
    print(f"Baseline: {base.shape[0]} rows x {base.shape[1]} cols | price '{price_col}' | currency {ccy}")
    unknown = set(base["destination"]) - set(DEST_TZ)
    if unknown:
        sys.exit(f"Add time zones for destinations {sorted(unknown)} to DEST_TZ.")

    t0 = parse_naive_timestamps(base["collected_at"]).max()
    END = t0.normalize() - pd.Timedelta(days=1)
    max_lead = int(LEADS.max())
    price_nan_rate = base[price_col].isna().mean()
    level_nan_rate = base["price_level"].isna().mean() if "price_level" in base else 0.0
    level_share = base["price_level"].value_counts(normalize=True) if "price_level" in base else None

    fx_spot, fx_lag, cpi = build_macro(t0, END, rng, a.fx_csv)
    max_horizon = max(
        END + pd.Timedelta(days=max_lead + 5),
        pd.to_datetime(base["flight_date"]).max() + pd.Timedelta(days=5),
    )
    all_dates = pd.date_range(START, max_horizon, freq="D")
    season = build_season(all_dates)
    sens = estimate_route_sensitivity(base, price_col, season, t0)

    tmpl = base.dropna(subset=[price_col]).copy()
    tmpl = tmpl[tmpl[price_col] > 0].reset_index(drop=True)
    tmpl["route_sens"] = tmpl["destination"].map(sens).fillna(1.0)
    tmpl["group"] = tmpl["destination"].map(GROUP).fillna("REGIONAL")
    tmpl["egypt_based"] = tmpl["airline"].astype(str).apply(lambda s: any(n in s for n in EGYPT_BASED))
    fd0 = pd.to_datetime(tmpl["flight_date"])
    lead0 = (fd0 - t0.normalize()).dt.days.to_numpy()
    s0 = np.array([season[g][(d - START).days] for g, d in zip(tmpl["group"], fd0)])
    det0 = (
        lead_curve(lead0, tmpl["route_sens"].to_numpy())
        * s0
        * np.array([DOW_MULT[d.dayofweek] for d in fd0])
    )
    tmpl["base_fare"] = tmpl[price_col] / det0
    tmpl["tod"] = pd.to_datetime(tmpl["departure_time"]).dt.strftime("%H:%M:%S")

    routes = sorted(tmpl["destination"].unique())
    r_idx = {r: i for i, r in enumerate(routes)}
    n_days = (END - START).days + 1
    market = np.zeros((len(routes), n_days))
    promo = np.ones((len(routes), n_days))
    for i in range(len(routes)):
        eps = rng.normal(0, MARKET_SD, n_days)
        for t in range(1, n_days):
            market[i, t] = MARKET_PHI * market[i, t - 1] + eps[t]
        for t in np.flatnonzero(rng.random(n_days) < PROMO_START_PROB):
            promo[i, t:t + rng.integers(*PROMO_LEN)] = rng.uniform(*PROMO_DEPTH)

    boundaries = [START + pd.DateOffset(years=k) for k in range(4)]
    bounds_np = np.array(boundaries[1:], dtype="datetime64[ns]")

    def block_of(dates):
        return np.searchsorted(bounds_np, dates.to_numpy(dtype="datetime64[ns]"), side="right")

    target_total = 3 * a.rows_per_year
    frames, n_trips_batch, batches = [], max(500, int(target_total / 14)), 0
    seen: set = set()
    tmpl_key = (tmpl["flight_number"].astype(str) + "|" + tmpl["travel_class"].astype(str)).to_numpy()
    next_trip_id = 0

    while True:
        n = n_trips_batch
        fdays = rng.integers(1, (END - START).days + max_lead + 1, n)
        ti = rng.integers(0, len(tmpl), n)
        ok = np.ones(n, bool)
        for i in range(n):
            key = (tmpl_key[ti[i]], fdays[i])
            if key in seen:
                ok[i] = False
            else:
                seen.add(key)
        fdays, ti, n = fdays[ok], ti[ok], int(ok.sum())
        if n == 0:
            batches += 1
            if batches >= 12:
                break
            continue
        fdate = START + pd.to_timedelta(fdays, unit="D")
        trip_ids = np.arange(next_trip_id, next_trip_id + n)
        next_trip_id += n

        coll = fdate.to_numpy()[:, None] - pd.to_timedelta(LEADS, unit="D").to_numpy()[None, :]
        coll = pd.DatetimeIndex(coll.ravel()).to_numpy().reshape(n, len(LEADS))
        keep = (
            (coll >= np.datetime64(START))
            & (coll <= np.datetime64(END))
            & (rng.random((n, len(LEADS))) < OBS_KEEP_PROB)
        )
        drift = np.zeros((n, len(LEADS)))
        drift[:, -1] = rng.normal(0, TRIP_INIT_SD, n)
        for j in range(len(LEADS) - 2, -1, -1):
            drift[:, j] = TRIP_PHI * drift[:, j + 1] + rng.normal(0, TRIP_STEP_SD, n)
        noise = rng.normal(0, NOISE_SD, (n, len(LEADS)))
        rows, cols_j = np.where(keep)
        frames.append(pd.DataFrame({
            "trip_id": trip_ids[rows],
            "tmpl": ti[rows],
            "flight_date": fdate[rows],
            "lead": LEADS[cols_j],
            "collected_date": pd.DatetimeIndex(coll[rows, cols_j]).normalize(),
            "drift": drift[rows, cols_j],
            "noise": noise[rows, cols_j],
        }))
        batches += 1
        allf = pd.concat(frames, ignore_index=True)
        counts = np.bincount(block_of(allf["collected_date"]), minlength=3)
        if counts.min() >= a.rows_per_year or batches >= 12:
            break

    # Downsample by whole trip_id to keep same-trip price trajectories intact
    trip_meta = allf.groupby("trip_id").agg(
        mid_date=("collected_date", "median"),
        n_rows=("lead", "size"),
    ).reset_index()
    trip_meta["block"] = block_of(trip_meta["mid_date"])
    kept_trip_ids = []
    for b in range(3):
        sub = trip_meta[trip_meta["block"] == b].sample(frac=1.0, random_state=a.seed + b)
        cum = sub["n_rows"].cumsum()
        chosen = sub.loc[cum <= a.rows_per_year, "trip_id"]
        if chosen.empty and not sub.empty:
            chosen = sub["trip_id"].iloc[:1]
        kept_trip_ids.append(chosen.to_numpy())
    selected_trips = set(np.concatenate(kept_trip_ids))
    g = allf[allf["trip_id"].isin(selected_trips)].sort_values(["collected_date", "tmpl"]).reset_index(drop=True)
    T = tmpl.iloc[g["tmpl"].to_numpy()].reset_index(drop=True)

    for al, first in AIRLINE_FIRST_SERVICE.items():
        bad = T["airline"].str.contains(al, regex=False) & (g["flight_date"] < pd.Timestamp(first))
        g, T = g[~bad].reset_index(drop=True), T[~bad].reset_index(drop=True)

    fday = (g["flight_date"] - START).dt.days.to_numpy()
    cday = (g["collected_date"] - START).dt.days.to_numpy()
    lead_t = lead_curve(g["lead"].to_numpy(), T["route_sens"].to_numpy())
    seas = np.array([season[gr][i] for gr, i in zip(T["group"], fday)])
    dow = np.array([DOW_MULT[d.dayofweek] for d in g["flight_date"]])
    usd_idx = (1 + USD_AIRFARE_INFLATION) ** (-(t0.normalize() - g["collected_date"]).dt.days.to_numpy() / 365.25)
    cd = g["collected_date"]
    fx_term = (fx_lag.reindex(cd).to_numpy()) ** FX_PASS_THROUGH
    if ccy == "EGP":
        w = np.where(T["egypt_based"], LOCAL_COST_WEIGHT["egypt_based"], LOCAL_COST_WEIGHT["foreign"])
        macro = (1 - w) * usd_idx * fx_term + w * cpi.reindex(cd).to_numpy()
    else:
        macro = usd_idx
    ri = T["destination"].map(r_idx).to_numpy()
    z = market[ri, cday] + g["drift"].to_numpy() + g["noise"].to_numpy() + np.log(promo[ri, cday])
    price = np.clip(
        T["base_fare"].to_numpy() * lead_t * seas * dow * macro * np.exp(z),
        PRICE_FLOOR_FRAC * base[price_col].min(),
        PRICE_CAP_MULT * base[price_col].max(),
    )
    price = np.round(price)

    if level_share is not None:
        p_low, p_high = level_share.get("low", 0.11), level_share.get("high", 0.25)
        lo, hi = np.quantile(z, [p_low, 1 - p_high])
        level = np.where(z < lo, "low", np.where(z > hi, "high", "typical")).astype(object)
        level[rng.random(len(z)) < level_nan_rate] = np.nan
    price = price.astype(float)
    price[rng.random(len(price)) < price_nan_rate] = np.nan

    run_secs = dict(zip(cd.unique(), rng.integers(0, 86400, cd.nunique())))
    collected_at = cd + pd.to_timedelta(cd.map(run_secs), unit="s")
    dep_naive = pd.DatetimeIndex(g["flight_date"].dt.strftime("%Y-%m-%d") + " " + T["tod"])
    step = np.where(T["stops"].to_numpy() == 0, 2, 6)
    duration = np.maximum(30, T["duration_min"].to_numpy() + 5 * rng.integers(-step, step + 1))
    arrival = compute_arrival(dep_naive, duration, T["destination"].to_numpy())

    out = T[cols].copy()
    out["collected_at"] = collected_at.dt.strftime("%Y-%m-%dT%H:%M:%S").to_numpy()
    out["flight_date"] = g["flight_date"].dt.strftime("%Y-%m-%d").to_numpy()
    out["departure_time"] = dep_naive.strftime("%Y-%m-%d %H:%M:%S")
    out["arrival_time"] = pd.DatetimeIndex(arrival).strftime("%Y-%m-%d %H:%M:%S")
    out["duration_min"] = duration
    out[price_col] = price
    if level_share is not None:
        out["price_level"] = level
    for c in cols:
        if pd.api.types.is_integer_dtype(dtypes[c]):
            out[c] = out[c].astype(dtypes[c])

    if ccy != "EGP":
        rate = fx_spot.reindex(cd).to_numpy()
        out["usd_to_egp_rate"] = np.round(rate, 4)
        out["price_egp"] = np.round(price * rate, 0)
    elif not a.no_fx_columns:
        rate = fx_spot.reindex(cd).to_numpy()
        out["usd_to_egp_rate"] = np.round(rate, 4)
        out["price_usd"] = np.round(price / rate, 2)
    if not a.no_provenance_col:
        out["is_synthetic"] = True

    assert (out[price_col].dropna() > 0).all(), "negative/zero price"
    assert (pd.to_datetime(out["flight_date"]) > pd.to_datetime(out["collected_at"]).dt.normalize()).all(), "date order"
    if "layovers" in out.columns and "stops" in out.columns:
        assert ((out["layovers"].isna()) == (out["stops"] == 0)).all(), "layover rule"
    assert pd.to_datetime(out["collected_at"]).max() < t0, "history must end before the baseline"
    assert not out.duplicated(["flight_number", "travel_class", "flight_date", "collected_at"]).any(), "duplicate offer"

    out = out.sort_values(["collected_at", "destination", "flight_number"]).reset_index(drop=True)
    out.to_csv(a.out, index=False)

    yr = block_of(pd.to_datetime(out["collected_at"]).dt.normalize())
    print(f"\nWrote {len(out):,} rows to {a.out} ({batches} generation batch(es))")
    summary = out.assign(block=yr).groupby("block").agg(
        rows=(price_col, "size"),
        median_price=(price_col, "median"),
    )
    print(summary.round(0).to_string())


if __name__ == "__main__":
    main()
