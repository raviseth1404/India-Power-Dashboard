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
import lightgbm as lgb
import numpy as np
import pandas as pd

from common import PARAMS, load_features, engineer

BACKTEST_START = "2023-01-01"


def metrics(actual, pred):
    e = pred - actual
    return {"MAE": float(np.mean(np.abs(e))),
            "RMSE": float(np.sqrt(np.mean(e ** 2))),
            "MAPE%": float(np.mean(np.abs(e / actual)) * 100)}


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
