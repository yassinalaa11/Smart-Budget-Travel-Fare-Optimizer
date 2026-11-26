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

USD_PRICE_INFLATION = {
    "GULF": 0.04, "HOLY": 0.04, "EUROPE_LEISURE": 0.05,
    "EUROPE_BIZ": 0.04, "REGIONAL": 0.05,
}
FX_PASS_THROUGH = 1.0
FX_HALFLIFE_DAYS = 3
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

GROUP = {
    "DXB": "GULF", "DOH": "GULF", "KWI": "GULF", "AUH": "GULF", "JED": "HOLY", "RUH": "HOLY",
    "ATH": "EUROPE_LEISURE", "FCO": "EUROPE_LEISURE", "CDG": "EUROPE_LEISURE",
    "LHR": "EUROPE_LEISURE", "FRA": "EUROPE_BIZ", "IST": "REGIONAL", "AMM": "REGIONAL",
    "JFK": "EUROPE_BIZ", "YYZ": "EUROPE_BIZ", "BOM": "REGIONAL",
}
GROUPS = ["GULF", "HOLY", "EUROPE_LEISURE", "EUROPE_BIZ", "REGIONAL"]
MONTH_MULT = {
    "GULF":           [1.25, 1.20, 1.10, .95, .78, .68, .68, .70, .80, 1.05, 1.20, 1.30],
    "HOLY":           [1.15, 1.10, 1.05, .95, .90, .90, .95, .92, .92, 1.05, 1.12, 1.18],
    "EUROPE_LEISURE": [.80, .82, .92, 1.05, 1.12, 1.18, 1.25, 1.28, 1.15, 1.00, .85, .95],
    "EUROPE_BIZ":     [.90, .95, 1.05, 1.05, 1.05, 1.00, .92, .90, 1.10, 1.15, 1.05, .90],
    "REGIONAL":       [.82, .82, .94, 1.05, 1.08, 1.12, 1.24, 1.24, 1.08, 1.00, .88, .95],
}
DOW_MULT = {
    "GULF":           [.98, 1.00, 1.00, 1.04, 1.10, 1.08, .95],
    "HOLY":           [.96, .96, .97, 1.06, 1.10, 1.06, .97],
    "EUROPE_LEISURE": [.93, .93, .95, 1.00, 1.12, 1.14, .98],
    "EUROPE_BIZ":     [1.06, 1.10, 1.10, 1.05, .90, .85, .88],
    "REGIONAL":       [.95, .95, .97, 1.02, 1.10, 1.10, .98],
}
HOLIDAY_MULT = {
    "ramadan": {"GULF": .88, "HOLY": 1.20, "EUROPE_LEISURE": 1.0, "EUROPE_BIZ": 1.0, "REGIONAL": .93},
    "fitr":    {"GULF": 1.15, "HOLY": 1.25, "EUROPE_LEISURE": 1.05, "EUROPE_BIZ": 1.0, "REGIONAL": 1.12},
    "adha":    {"GULF": 1.12, "HOLY": 1.35, "EUROPE_LEISURE": 1.05, "EUROPE_BIZ": 1.0, "REGIONAL": 1.12},
    "xmas":    {"GULF": 1.35, "HOLY": 1.10, "EUROPE_LEISURE": 1.30, "EUROPE_BIZ": .90, "REGIONAL": 1.15},
    "easter":  {"GULF": 1.0, "HOLY": 1.0, "EUROPE_LEISURE": 1.15, "EUROPE_BIZ": 1.0, "REGIONAL": 1.08},
}
HOLIDAYS = [
    ("ramadan", "2024-03-11", "2024-04-09"), ("fitr", "2024-04-08", "2024-04-16"), ("adha", "2024-06-13", "2024-06-22"),
    ("ramadan", "2025-03-01", "2025-03-29"), ("fitr", "2025-03-28", "2025-04-05"), ("adha", "2025-06-03", "2025-06-12"),
    ("ramadan", "2026-02-18", "2026-03-19"), ("fitr", "2026-03-18", "2026-03-26"), ("adha", "2026-05-24", "2026-06-02"),
    ("ramadan", "2027-02-08", "2027-03-09"), ("fitr", "2027-03-08", "2027-03-16"),
    ("xmas", "2023-12-20", "2024-01-04"), ("xmas", "2024-12-20", "2025-01-04"),
    ("xmas", "2025-12-20", "2026-01-04"), ("xmas", "2026-12-20", "2027-01-04"),
    ("easter", "2024-03-28", "2024-04-01"), ("easter", "2025-04-17", "2025-04-21"),
    ("easter", "2026-04-02", "2026-04-06"), ("easter", "2027-03-25", "2027-03-29"),
]

LAST_MIN_STRENGTH = 0.30
EARLY_BIRD_DISCOUNT = 0.05
SOLD_OUT_BASE, SOLD_OUT_DEMAND = 0.01, 0.35

NOISE_SD = 0.03
TRIP_INIT_SD = 0.04
TRIP_STEP_SD, TRIP_PHI = 0.025, 0.85
MARKET_SD, MARKET_PHI = 0.010, 0.97
PROMO_START_PROB, PROMO_LEN, PROMO_DEPTH = 0.015, (3, 10), (0.88, 0.95)
PRICE_FLOOR_FRAC, PRICE_CAP_MULT = 0.25, 1.5

REVIEW_GROWTH_PER_YEAR = 0.25
NEW_PROPERTY_REVIEWS = 100
NEW_PROPERTY_AGE_DAYS = (60, 540)
RATING_DRIFT_SD = 0.04


def parse_naive_timestamps(series: pd.Series) -> pd.Series:
    parsed = pd.to_datetime(series, utc=True, format="mixed")
    return parsed.dt.tz_localize(None)


def detect_price_columns(df: pd.DataFrame) -> tuple[str, str | None, str]:
    num = lambda c: pd.api.types.is_numeric_dtype(df[c])
    night = [c for c in df.columns if c.lower().startswith("price_per_night") and num(c)]
    total = [c for c in df.columns if c.lower().startswith("total_price") and num(c)]
    if not night:
        sys.exit("No numeric 'price_per_night*' column found in the baseline file.")
    if "currency" in df.columns:
        ccy = str(df["currency"].dropna().mode().iat[0]).upper()
    elif "_" in night[0] and night[0].split("_")[-1].isalpha() and len(night[0].split("_")[-1]) == 3:
        ccy = night[0].split("_")[-1].upper()
    else:
        ccy = "USD"
    return night[0], (total[0] if total else None), ccy


def interp_series(anchors, index: pd.DatetimeIndex, log: bool = True) -> np.ndarray:
    xs = np.array([(pd.Timestamp(d) - START).days for d, _ in anchors], float)
    ys = np.array([v for _, v in anchors], float)
    out = np.interp((index - START).days.to_numpy(float), xs, np.log(ys) if log else ys)
    return np.exp(out) if log else out


def build_fx(t0: pd.Timestamp, rng, fx_csv: str | None):
    idx = pd.date_range(START - pd.Timedelta(days=30), t0.normalize(), freq="D")
    if fx_csv:
        raw = pd.read_csv(fx_csv, parse_dates=["date"]).set_index("date")["usd_egp"]
        fx = raw.reindex(idx).ffill().bfill().to_numpy(float)
    else:
        fx = interp_series(FX_ANCHORS, idx)
        post = idx >= pd.Timestamp("2024-03-06")
        fx = fx * np.exp(rng.normal(0, np.where(post, 0.0015, 0.0003)))
    spot = pd.Series(fx, index=idx)
    lag = np.exp(np.log(spot).ewm(halflife=FX_HALFLIFE_DAYS, adjust=False).mean())
    return spot, lag / lag.loc[t0.normalize()]


def build_tables(dates: pd.DatetimeIndex) -> tuple[np.ndarray, np.ndarray]:
    season = np.zeros((len(GROUPS), len(dates)))
    for gi, g in enumerate(GROUPS):
        m = np.array([MONTH_MULT[g][d.month - 1] for d in dates], float)
        for kind, s, e in HOLIDAYS:
            m[(dates >= pd.Timestamp(s)) & (dates <= pd.Timestamp(e))] *= HOLIDAY_MULT[kind][g]
        season[gi] = pd.Series(m).rolling(5, center=True, min_periods=1).mean().to_numpy()
    return season, np.array([DOW_MULT[g] for g in GROUPS])


def lead_curve(lead: np.ndarray, demand: np.ndarray) -> np.ndarray:
    last_min = 1 + LAST_MIN_STRENGTH * np.exp(-lead / 4) * np.tanh(2 * (demand - 1))
    early = 1 - EARLY_BIRD_DISCOUNT * np.clip((lead - 30) / 60, 0, 1)
    return last_min * early


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--baseline", default="data/destination_hotels.csv")
    ap.add_argument("--out", default="data/historical_hotels_3yrs.csv")
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
    night_col, total_col, ccy = detect_price_columns(base)
    print(f"Baseline: {base.shape[0]} rows x {base.shape[1]} cols | nightly '{night_col}' | currency {ccy}")
    unknown = set(base["destination"]) - set(GROUP)
    if unknown:
        sys.exit(f"Add destinations {sorted(unknown)} to GROUP.")

    # Drop unpriced properties before back-solving base rates
    tmpl = base.dropna(subset=[night_col]).copy()
    tmpl = tmpl[tmpl[night_col] > 0].drop_duplicates(subset=["destination", "name"]).reset_index(drop=True)

    t0 = parse_naive_timestamps(base["collected_at"]).max()
    END = t0.normalize() - pd.Timedelta(days=1)
    nights = int((pd.to_datetime(tmpl["check_out"]) - pd.to_datetime(tmpl["check_in"])).dt.days.mode().iat[0])
    max_lead = int(LEADS.max())

    fx_spot, fx_lag = build_fx(t0, rng, a.fx_csv)
    ci0 = pd.to_datetime(tmpl["check_in"])
    max_date = max(END + pd.Timedelta(days=max_lead + 5), ci0.max() + pd.Timedelta(days=5))
    dates = pd.date_range(START, max_date, freq="D")
    season_mat, dow_mat = build_tables(dates)
    gidx = {g: i for i, g in enumerate(GROUPS)}

    tmpl["gi"] = tmpl["destination"].map(GROUP).map(gidx)
    lead0 = (ci0 - t0.normalize()).dt.days.to_numpy()
    di0 = (ci0 - START).dt.days.to_numpy()
    dem0 = season_mat[tmpl["gi"], di0] * dow_mat[tmpl["gi"], ci0.dt.dayofweek.to_numpy()]
    tmpl["base_rate"] = tmpl[night_col] / (lead_curve(lead0, dem0) * dem0)

    reviews_clean = pd.to_numeric(tmpl["reviews"], errors="coerce") if "reviews" in tmpl else pd.Series(np.nan, index=tmpl.index)
    new = (reviews_clean.fillna(NEW_PROPERTY_REVIEWS + 1) < NEW_PROPERTY_REVIEWS).to_numpy()
    age = rng.integers(*NEW_PROPERTY_AGE_DAYS, len(tmpl))
    tmpl["open_date"] = pd.to_datetime(np.where(
        new,
        (t0.normalize() - pd.to_timedelta(age, unit="D")).to_numpy(),
        np.datetime64("NaT"),
    ))
    tmpl["rating_slope"] = rng.normal(0, RATING_DRIFT_SD, len(tmpl))
    print(f"Priced properties: {len(tmpl)} | newly opened: {int(new.sum())} | stay length: {nights} nights")

    routes = sorted(tmpl["destination"].unique())
    r_idx = {r: i for i, r in enumerate(routes)}
    n_days = (END - START).days + 1
    market, promo = np.zeros((len(routes), n_days)), np.ones((len(routes), n_days))
    for i in range(len(routes)):
        eps = rng.normal(0, MARKET_SD, n_days)
        for t in range(1, n_days):
            market[i, t] = MARKET_PHI * market[i, t - 1] + eps[t]
        for t in np.flatnonzero(rng.random(n_days) < PROMO_START_PROB):
            promo[i, t:t + rng.integers(*PROMO_LEN)] = rng.uniform(*PROMO_DEPTH)

    boundaries = [START + pd.DateOffset(years=k) for k in range(4)]
    bounds_np = np.array(boundaries[1:], dtype="datetime64[ns]")
    block_of = lambda d: np.searchsorted(bounds_np, d.to_numpy(dtype="datetime64[ns]"), side="right")
    target_total = 3 * a.rows_per_year
    seen: set = set()
    frames, batches, n_stays = [], 0, max(500, int(target_total / 12))
    next_stay_id = 0

    while True:
        n = n_stays
        cin_days = rng.integers(1, (END - START).days + max_lead + 1, n)
        ti = rng.integers(0, len(tmpl), n)
        ok = np.ones(n, bool)
        for i in range(n):
            key = (ti[i], cin_days[i])
            ok[i] = key not in seen
            seen.add(key)
        cin_days, ti, n = cin_days[ok], ti[ok], int(ok.sum())
        if n == 0:
            batches += 1
            if batches >= 12:
                break
            continue
        cin = START + pd.to_timedelta(cin_days, unit="D")
        stay_ids = np.arange(next_stay_id, next_stay_id + n)
        next_stay_id += n

        coll = cin.to_numpy()[:, None] - pd.to_timedelta(LEADS, unit="D").to_numpy()[None, :]
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
        r, c = np.where(keep)
        g = pd.DataFrame({
            "stay_id": stay_ids[r],
            "tmpl": ti[r],
            "check_in": cin[r],
            "lead": LEADS[c],
            "collected_date": pd.DatetimeIndex(coll[r, c]).normalize(),
            "drift": drift[r, c],
            "noise": noise[r, c],
        })
        opened = tmpl["open_date"].to_numpy()[g["tmpl"]]
        g = g[~(pd.notna(opened) & (g["collected_date"].to_numpy() < opened))]
        gi = tmpl["gi"].to_numpy()[g["tmpl"]]
        dem = (
            season_mat[gi, (g["check_in"] - START).dt.days.to_numpy()]
            * dow_mat[gi, g["check_in"].dt.dayofweek.to_numpy()]
        )
        p_sold = np.clip(
            SOLD_OUT_BASE + SOLD_OUT_DEMAND * np.clip(dem - 1.1, 0, None) * np.exp(-g["lead"].to_numpy() / 12),
            0, 0.5,
        )
        g = g[rng.random(len(g)) >= p_sold]
        frames.append(g)
        batches += 1
        allf = pd.concat(frames, ignore_index=True)
        counts = np.bincount(block_of(allf["collected_date"]), minlength=3)
        if counts.min() >= a.rows_per_year or batches >= 12:
            break

    stay_meta = allf.groupby("stay_id").agg(
        mid_date=("collected_date", "median"),
        n_rows=("lead", "size"),
    ).reset_index()
    stay_meta["block"] = block_of(stay_meta["mid_date"])
    kept_stay_ids = []
    for b in range(3):
        sub = stay_meta[stay_meta["block"] == b].sample(frac=1.0, random_state=a.seed + b)
        cum = sub["n_rows"].cumsum()
        chosen = sub.loc[cum <= a.rows_per_year, "stay_id"]
        if chosen.empty and not sub.empty:
            chosen = sub["stay_id"].iloc[:1]
        kept_stay_ids.append(chosen.to_numpy())
    selected_stays = set(np.concatenate(kept_stay_ids))
    g = allf[allf["stay_id"].isin(selected_stays)].sort_values(["collected_date", "tmpl"]).reset_index(drop=True)
    T = tmpl.iloc[g["tmpl"].to_numpy()].reset_index(drop=True)

    gi = T["gi"].to_numpy()
    cday = (g["collected_date"] - START).dt.days.to_numpy()
    cin_i = (g["check_in"] - START).dt.days.to_numpy()
    dem = season_mat[gi, cin_i] * dow_mat[gi, g["check_in"].dt.dayofweek.to_numpy()]
    years_back = (t0.normalize() - g["collected_date"]).dt.days.to_numpy() / 365.25
    infl = np.array([USD_PRICE_INFLATION[GROUPS[i]] for i in gi])
    macro = (1 + infl) ** (-years_back)
    if ccy == "EGP":
        macro = macro * fx_lag.reindex(g["collected_date"]).to_numpy() ** FX_PASS_THROUGH
    ri = T["destination"].map(r_idx).to_numpy()
    z = market[ri, cday] + g["drift"].to_numpy() + g["noise"].to_numpy() + np.log(promo[ri, cday])
    raw = np.clip(
        T["base_rate"].to_numpy() * lead_curve(g["lead"].to_numpy(), dem) * dem * macro * np.exp(z),
        PRICE_FLOOR_FRAC * tmpl[night_col].min(),
        PRICE_CAP_MULT * tmpl[night_col].max(),
    )

    cd = g["collected_date"]
    run_secs = dict(zip(cd.unique(), rng.integers(0, 86400, cd.nunique())))
    collected_at = cd + pd.to_timedelta(cd.map(run_secs), unit="s")
    out = T[cols].copy()
    out["collected_at"] = collected_at.dt.strftime("%Y-%m-%dT%H:%M:%S").to_numpy()
    out["check_in"] = g["check_in"].dt.strftime("%Y-%m-%d").to_numpy()
    out["check_out"] = (g["check_in"] + pd.Timedelta(days=nights)).dt.strftime("%Y-%m-%d").to_numpy()
    out[night_col] = np.round(raw)
    if total_col:
        out[total_col] = np.round(raw * nights)
    if "reviews" in cols:
        rev_num = pd.to_numeric(T["reviews"], errors="coerce")
        est = rev_num * np.exp(-np.log(1 + REVIEW_GROWTH_PER_YEAR) * years_back)
        life = ((cd - T["open_date"]).dt.days / (t0.normalize() - T["open_date"]).dt.days).clip(0.02, 1)
        out["reviews"] = np.maximum(1, np.round(np.where(T["open_date"].notna(), rev_num * life, est)))
        out.loc[rev_num.isna().to_numpy(), "reviews"] = np.nan
    if "rating" in cols:
        rat_num = pd.to_numeric(T["rating"], errors="coerce")
        out["rating"] = np.round(np.clip(rat_num + T["rating_slope"] * years_back, 1, 5), 1)
    for c in cols:
        if pd.api.types.is_integer_dtype(dtypes[c]) and not out[c].isna().any():
            out[c] = out[c].astype(dtypes[c])

    if ccy != "EGP":
        rate = fx_spot.reindex(cd).to_numpy()
        out["usd_to_egp_rate"] = np.round(rate, 4)
        out["price_per_night_egp"] = np.round(out[night_col].to_numpy() * rate)
        if total_col:
            out["total_price_egp"] = np.round(out[total_col].to_numpy() * rate)
    elif not a.no_fx_columns:
        rate = fx_spot.reindex(cd).to_numpy()
        out["usd_to_egp_rate"] = np.round(rate, 4)
        out["price_per_night_usd"] = np.round(out[night_col].to_numpy() / rate, 2)
        if total_col:
            out["total_price_usd"] = np.round(out[total_col].to_numpy() / rate, 2)
    if not a.no_provenance_col:
        out["is_synthetic"] = True

    assert (out[night_col] > 0).all(), "non-positive price"
    assert ((pd.to_datetime(out["check_out"]) - pd.to_datetime(out["check_in"])).dt.days == nights).all()
    assert (pd.to_datetime(out["check_in"]) > pd.to_datetime(out["collected_at"]).dt.normalize()).all(), "date order"
    assert pd.to_datetime(out["collected_at"]).max() < t0, "history must end before the baseline"
    assert not out.duplicated(["name", "destination", "check_in", "collected_at"]).any(), "duplicate offer"

    out = out.sort_values(["collected_at", "destination", "name"]).reset_index(drop=True)
    out.to_csv(a.out, index=False)

    yr = block_of(pd.to_datetime(out["collected_at"]).dt.normalize())
    print(f"\nWrote {len(out):,} rows to {a.out} ({batches} generation batch(es))")
    summary = out.assign(block=yr).groupby("block").agg(
        rows=(night_col, "size"),
        median_nightly=(night_col, "median"),
    )
    print(summary.round(0).to_string())


if __name__ == "__main__":
    main()
