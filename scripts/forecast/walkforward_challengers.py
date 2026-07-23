"""
Walk-forward evaluation of the bake-off challengers over the SAME period as
the production backtest (2023-01 -> present):

- LightGBM  : monthly retrain on all prior history, full feature matrix (reference)
- XGBoost   : same protocol as LightGBM
- AutoETS   : daily h=1 refit on trailing 730 days (statsforecast) — its fair usage
- Ensembles : simple averages of the above, computed per day

Outputs MAE/MAPE per year vs actuals + saves challenger_predictions.csv.
"""
import warnings

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")

from common import PARAMS, load_features, engineer

START = "2023-01-01"


def metrics(a, p):
    e = p - a
    return np.mean(np.abs(e)), np.mean(np.abs(e / a)) * 100


def main():
    X, y = engineer(load_features())
    mask = y.notna()
    X, y = X[mask], y[mask]
    months = pd.date_range(START, y.index.max(), freq="MS")

    import lightgbm as lgb
    from xgboost import XGBRegressor

    rows = []
    for m0 in months:
        m1 = m0 + pd.offsets.MonthEnd(0)
        tr = X.index < m0
        te = (X.index >= m0) & (X.index <= m1)
        if te.sum() == 0:
            continue
        cap = X.loc[te, "price_cap"].values
        lg = lgb.LGBMRegressor(**PARAMS).fit(X[tr], y[tr])
        xg = XGBRegressor(n_estimators=700, learning_rate=0.04, max_depth=8,
                          subsample=0.9, colsample_bytree=0.85, reg_lambda=1.0,
                          objective="reg:absoluteerror", verbosity=0,
                          ).fit(X[tr].astype(float), y[tr])
        rows.append(pd.DataFrame({
            "date": X.index[te], "actual": y[te].values,
            "lgb": np.clip(lg.predict(X[te]), 0, cap),
            "xgb": np.clip(xg.predict(X[te].astype(float)), 0, cap),
        }))
        print(f"GBM {m0:%Y-%m} done", flush=True)
    bt = pd.concat(rows).set_index("date")

    # AutoETS: daily h=1 refit on trailing 730 days (vectorised via statsforecast
    # cross_validation, which rolls the origin one day at a time).
    from statsforecast import StatsForecast
    from statsforecast.models import AutoETS
    hist = y.sort_index()
    sdf = pd.DataFrame({"unique_id": "dam", "ds": hist.index, "y": hist.values})
    n_windows = int((hist.index >= START).sum())
    sf = StatsForecast(models=[AutoETS(season_length=7)], freq="D")
    cv = sf.cross_validation(df=sdf, h=1, step_size=1, n_windows=n_windows,
                             input_size=730, refit=True)
    ets = cv.set_index("ds")["AutoETS"]
    print("AutoETS done", flush=True)

    bt["ets"] = ets.reindex(bt.index)
    bt = bt.dropna()
    bt["ens_lgb_ets"] = (bt.lgb + bt.ets) / 2
    bt["ens_lgb_xgb"] = (bt.lgb + bt.xgb) / 2
    bt["ens_all3"] = (bt.lgb + bt.xgb + bt.ets) / 3
    bt.to_csv("challenger_predictions.csv")

    models = ["lgb", "xgb", "ets", "ens_lgb_xgb", "ens_lgb_ets", "ens_all3"]
    print(f"\n=== WALK-FORWARD {START} -> {bt.index.max().date()}  (n={len(bt)}) ===")
    print(f"{'model':<14}" + "".join(f"{yr:>14}" for yr in sorted(bt.index.year.unique())) + f"{'ALL MAPE%':>12}{'ALL MAE':>9}")
    for m in models:
        cells = ""
        for yr in sorted(bt.index.year.unique()):
            g = bt[bt.index.year == yr]
            _, mape = metrics(g.actual, g[m])
            cells += f"{mape:>13.1f}%"
        mae, mape = metrics(bt.actual, bt[m])
        print(f"{m:<14}{cells}{mape:>11.1f}%{mae:>9.0f}")


if __name__ == "__main__":
    main()
