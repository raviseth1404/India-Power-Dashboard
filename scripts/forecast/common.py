"""Shared data loading + feature engineering for the DAM price model.
Used by train_backtest.py (walk-forward evaluation) and daily_forecast.py
(production daily run) so the two can never drift apart."""
import json
import urllib.request

import holidays as holidays_pkg
import numpy as np
import pandas as pd

SUPABASE_URL = "https://ltzulzadxqpwvfksmcfa.supabase.co"
ANON = ("eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6Imx0enVsemFkeHFwd3Zma3NtY2ZhIiwicm9sZSI6ImFub24iLCJpYXQiOjE3ODQwNzc2MTAsImV4cCI6MjA5OTY1MzYxMH0."
        "0zDXrCDV1K9J7kruXWEUbQFSbvPEI_QddgfGI6oBmZM")

NUM_COLS = [
    "dam_avg_mcp", "dam_min_mcp", "dam_max_mcp", "dam_sum_mcv_mw",
    "rtm_avg_mcp", "rtm_max_mcp", "rtm_sum_mcv_mw",
    "peak_demand_mw", "peak_shortage_mw", "energy_met_mu",
    "hydro_gen_mu", "wind_gen_mu", "solar_gen_mu",
    "outage_central_mw", "outage_state_mw", "outage_total_mw",
    "coal_mu", "gas_mu", "nuclear_mu", "res_mu", "res_share_pct",
    "wx_tmax_c", "wx_tmean_c", "wx_rain_mm", "wx_solar_mj",
    "wx_wind_kmh", "wx_nr_tmax_c",
]

PARAMS = dict(objective="regression_l1", n_estimators=700, learning_rate=0.04,
              num_leaves=63, min_child_samples=25, subsample=0.9,
              subsample_freq=1, colsample_bytree=0.85, reg_lambda=1.0,
              verbose=-1)


def rest_get(path, key=ANON):
    req = urllib.request.Request(
        f"{SUPABASE_URL}/rest/v1/{path}",
        headers={"apikey": key, "Authorization": f"Bearer {key}"})
    return json.load(urllib.request.urlopen(req, timeout=60))


def load_features():
    rows, offset = [], 0
    while True:
        chunk = rest_get(
            f"v_forecast_features?select=*&order=report_date.asc&offset={offset}&limit=1000")
        rows += chunk
        if len(chunk) < 1000:
            break
        offset += 1000
    df = pd.DataFrame(rows)
    df["report_date"] = pd.to_datetime(df["report_date"])
    for c in NUM_COLS:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    return df.sort_values("report_date").reset_index(drop=True)


def engineer(df):
    """df: v_forecast_features frame (may include a trailing target-day row
    with only weather filled). Returns (X, y) indexed by date."""
    d = df.copy().set_index("report_date")
    y = d["dam_avg_mcp"]

    f = pd.DataFrame(index=d.index)
    for lag in (1, 2, 3, 7, 14, 30, 365):
        f[f"mcp_lag{lag}"] = y.shift(lag)
    f["mcp_roll7"] = y.shift(1).rolling(7).mean()
    f["mcp_roll30"] = y.shift(1).rolling(30).mean()
    f["mcp_roll7_std"] = y.shift(1).rolling(7).std()
    f["mcp_max_lag1"] = d["dam_max_mcp"].shift(1)
    f["rtm_lag1"] = d["rtm_avg_mcp"].shift(1)
    f["dam_vol_lag1"] = d["dam_sum_mcv_mw"].shift(1)

    for col, lag in [("peak_demand_mw", 1), ("peak_demand_mw", 7),
                     ("energy_met_mu", 1), ("peak_shortage_mw", 1),
                     ("outage_total_mw", 1), ("outage_central_mw", 1),
                     ("solar_gen_mu", 1), ("wind_gen_mu", 1),
                     ("hydro_gen_mu", 1), ("res_share_pct", 1), ("coal_mu", 1)]:
        f[f"{col}_lag{lag}"] = d[col].shift(lag)
    f["demand_roll7"] = d["peak_demand_mw"].shift(1).rolling(7).mean()
    f["outage_roll7"] = d["outage_total_mw"].shift(1).rolling(7).mean()

    for c in ("wx_tmax_c", "wx_tmean_c", "wx_rain_mm", "wx_solar_mj",
              "wx_wind_kmh", "wx_nr_tmax_c"):
        f[c] = d[c]
    f["wx_tmax_anom7"] = d["wx_tmax_c"] - d["wx_tmax_c"].rolling(7).mean()
    f["wx_rain_roll7"] = d["wx_rain_mm"].rolling(7).sum()

    idx = f.index
    f["dow"] = idx.dayofweek
    f["month"] = idx.month
    f["doy_sin"] = np.sin(2 * np.pi * idx.dayofyear / 365.25)
    f["doy_cos"] = np.cos(2 * np.pi * idx.dayofyear / 365.25)
    f["is_weekend"] = (idx.dayofweek >= 5).astype(int)
    ind_h = holidays_pkg.India(years=range(2012, idx.year.max() + 2))
    f["is_holiday"] = [int(dt in ind_h) for dt in idx.date]

    f["price_cap"] = np.select(
        [idx < "2022-04-01", idx < "2023-04-01"], [20000, 12000], 10000)

    return f, y
