"""
Day-ahead DAM price model — v1 walk-forward backtest.

Predicts NEXT-day daily-average DAM MCP using only information available the
day before: lagged prices/fundamentals, calendar + holidays, and the target
day's weather (in production this comes from the weather forecast; the backtest
uses actual weather as its stand-in, which flatters results slightly — day-ahead
temperature forecasts for India are typically within ~1°C).

Walk-forward: for each month from BACKTEST_START, train on ALL prior history,
predict that month, roll forward. Reports MAE / RMSE / MAPE per year vs naive
baselines (yesterday's price, last week's price), plus monthly-average errors
and P10/P90 quantile coverage.

Usage: python3 train_backtest.py            (writes backtest_predictions.csv)
"""
import json
import urllib.request

import holidays as holidays_pkg
import lightgbm as lgb
import numpy as np
import pandas as pd

SUPABASE_URL = "https://ltzulzadxqpwvfksmcfa.supabase.co"
ANON = ("eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6Imx0enVsemFkeHFwd3Zma3NtY2ZhIiwicm9sZSI6ImFub24iLCJpYXQiOjE3ODQwNzc2MTAsImV4cCI6MjA5OTY1MzYxMH0."
        "0zDXrCDV1K9J7kruXWEUbQFSbvPEI_QddgfGI6oBmZM")
BACKTEST_START = "2023-01-01"

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


def load_features():
    rows, offset = [], 0
    while True:
        req = urllib.request.Request(
            f"{SUPABASE_URL}/rest/v1/v_forecast_features?select=*"
            f"&order=report_date.asc&offset={offset}&limit=1000",
            headers={"apikey": ANON, "Authorization": f"Bearer {ANON}"})
        chunk = json.load(urllib.request.urlopen(req, timeout=60))
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
    d = df.copy().set_index("report_date")
    y = d["dam_avg_mcp"]

    f = pd.DataFrame(index=d.index)
    # --- price history (as known the evening before delivery day) -----------
    for lag in (1, 2, 3, 7, 14, 30, 365):
        f[f"mcp_lag{lag}"] = y.shift(lag)
    f["mcp_roll7"] = y.shift(1).rolling(7).mean()
    f["mcp_roll30"] = y.shift(1).rolling(30).mean()
    f["mcp_roll7_std"] = y.shift(1).rolling(7).std()
    f["mcp_max_lag1"] = d["dam_max_mcp"].shift(1)
    f["rtm_lag1"] = d["rtm_avg_mcp"].shift(1)
    f["dam_vol_lag1"] = d["dam_sum_mcv_mw"].shift(1)

    # --- fundamentals, lagged (yesterday's PSP report is what you have) ------
    for col, lag in [("peak_demand_mw", 1), ("peak_demand_mw", 7),
                     ("energy_met_mu", 1), ("peak_shortage_mw", 1),
                     ("outage_total_mw", 1), ("outage_central_mw", 1),
                     ("solar_gen_mu", 1), ("wind_gen_mu", 1),
                     ("hydro_gen_mu", 1), ("res_share_pct", 1), ("coal_mu", 1)]:
        f[f"{col}_lag{lag}"] = d[col].shift(lag)
    f["demand_roll7"] = d["peak_demand_mw"].shift(1).rolling(7).mean()
    f["outage_roll7"] = d["outage_total_mw"].shift(1).rolling(7).mean()

    # --- weather for the DELIVERY day (forecastable day-ahead) ---------------
    for c in ("wx_tmax_c", "wx_tmean_c", "wx_rain_mm", "wx_solar_mj",
              "wx_wind_kmh", "wx_nr_tmax_c"):
        f[c] = d[c]
    f["wx_tmax_anom7"] = d["wx_tmax_c"] - d["wx_tmax_c"].rolling(7).mean()
    f["wx_rain_roll7"] = d["wx_rain_mm"].rolling(7).sum()

    # --- calendar -------------------------------------------------------------
    idx = f.index
    f["dow"] = idx.dayofweek
    f["month"] = idx.month
    f["doy_sin"] = np.sin(2 * np.pi * idx.dayofyear / 365.25)
    f["doy_cos"] = np.cos(2 * np.pi * idx.dayofyear / 365.25)
    f["is_weekend"] = (idx.dayofweek >= 5).astype(int)
    ind_h = holidays_pkg.India(years=range(2012, 2027))
    f["is_holiday"] = [int(dt in ind_h) for dt in idx.date]

    # --- market regime: DAM price cap by era ---------------------------------
    f["price_cap"] = np.select(
        [idx < "2022-04-01", idx < "2023-04-01"], [20000, 12000], 10000)

    return f, y


def metrics(actual, pred):
    e = pred - actual
    return {"MAE": float(np.mean(np.abs(e))),
            "RMSE": float(np.sqrt(np.mean(e ** 2))),
            "MAPE%": float(np.mean(np.abs(e / actual)) * 100)}


PARAMS = dict(objective="regression_l1", n_estimators=700, learning_rate=0.04,
              num_leaves=63, min_child_samples=25, subsample=0.9,
              subsample_freq=1, colsample_bytree=0.85, reg_lambda=1.0,
              verbose=-1)


def main():
    df = load_features()
    print(f"features loaded: {len(df)} days {df.report_date.min().date()} -> {df.report_date.max().date()}")
    X, y = engineer(df)
    mask = y.notna()
    X, y = X[mask], y[mask]

    months = pd.date_range(BACKTEST_START, y.index.max(), freq="MS")
    preds = []
    imp = None
    for m0 in months:
        m1 = m0 + pd.offsets.MonthEnd(0)
        tr = X.index < m0
        te = (X.index >= m0) & (X.index <= m1)
        if te.sum() == 0:
            continue
        model = lgb.LGBMRegressor(**PARAMS)
        model.fit(X[tr], y[tr])
        p = model.predict(X[te])
        q10 = lgb.LGBMRegressor(**{**PARAMS, "objective": "quantile", "alpha": 0.1}).fit(X[tr], y[tr]).predict(X[te])
        q90 = lgb.LGBMRegressor(**{**PARAMS, "objective": "quantile", "alpha": 0.9}).fit(X[tr], y[tr]).predict(X[te])
        cap = X.loc[te, "price_cap"].values
        preds.append(pd.DataFrame({
            "date": X.index[te], "actual": y[te].values,
            "pred": np.clip(p, 0, cap),
            "p10": np.clip(q10, 0, cap), "p90": np.clip(q90, 0, cap),
            "naive_lag1": X.loc[te, "mcp_lag1"].values,
            "naive_lag7": X.loc[te, "mcp_lag7"].values,
        }))
        imp = pd.Series(model.feature_importances_, index=X.columns)
        print(f"  {m0:%Y-%m}: trained on {tr.sum()} days, predicted {int(te.sum())}", flush=True)

    bt = pd.concat(preds).dropna(subset=["actual"])
    bt.to_csv("backtest_predictions.csv", index=False)

    print("\n=== DAILY DAM avg-price backtest (2023-01 -> present) ===")
    print(f"{'year':<6}{'n':>5} | {'model MAE':>10}{'MAPE%':>8} | {'lag1 MAE':>10}{'MAPE%':>8} | {'lag7 MAE':>10}{'MAPE%':>8}")
    for yr, g in bt.groupby(bt.date.dt.year):
        mm, m1_, m7 = metrics(g.actual, g.pred), metrics(g.actual, g.naive_lag1), metrics(g.actual, g.naive_lag7)
        print(f"{yr:<6}{len(g):>5} | {mm['MAE']:>10.0f}{mm['MAPE%']:>8.1f} | {m1_['MAE']:>10.0f}{m1_['MAPE%']:>8.1f} | {m7['MAE']:>10.0f}{m7['MAPE%']:>8.1f}")
    mm, m1_, m7 = metrics(bt.actual, bt.pred), metrics(bt.actual, bt.naive_lag1), metrics(bt.actual, bt.naive_lag7)
    print(f"{'ALL':<6}{len(bt):>5} | {mm['MAE']:>10.0f}{mm['MAPE%']:>8.1f} | {m1_['MAE']:>10.0f}{m1_['MAPE%']:>8.1f} | {m7['MAE']:>10.0f}{m7['MAPE%']:>8.1f}")

    cov = ((bt.actual >= bt.p10) & (bt.actual <= bt.p90)).mean() * 100
    print(f"\nP10-P90 band coverage: {cov:.1f}% (target ~80%)")

    mo = bt.set_index("date").resample("MS")[["actual", "pred"]].mean().dropna()
    mo_err = metrics(mo.actual, mo.pred)
    print(f"MONTHLY-average level: MAE ₹{mo_err['MAE']:.0f}  MAPE {mo_err['MAPE%']:.1f}%  ({len(mo)} months)")

    print("\nTop 15 features (last model):")
    for k, v in imp.sort_values(ascending=False).head(15).items():
        print(f"  {k:<24}{v}")


if __name__ == "__main__":
    main()
