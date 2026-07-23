"""
Open-source model bake-off for the DAM daily-average price.

Same protocol for every engine, per target day D in {23, 24, 25 Jul 2026}:
train ONLY on data with date < D, predict D. The GBM family (LightGBM,
XGBoost, CatBoost) shares our full feature matrix (lags, fundamentals,
weather, calendar). The statistical family (AutoARIMA, AutoETS, SeasonalNaive
via Nixtla statsforecast; Prophet) is univariate on the price series
(last 3 years), which is their standard usage.

Actuals: 23 Jul = 5375.03, 24 Jul = 4465.15, 25 Jul = TBD (auction on the 24th).
"""
import json
import urllib.request
import warnings

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")

from common import SUPABASE_URL, ANON, PARAMS, load_features, engineer

TARGETS = ["2026-07-23", "2026-07-24", "2026-07-25"]
ACTUALS = {"2026-07-23": 5375.03, "2026-07-24": 4465.15, "2026-07-25": None}
STAT_WINDOW_DAYS = 3 * 365


def national_weather(day_iso):
    req = urllib.request.Request(
        f"{SUPABASE_URL}/rest/v1/weather_daily?report_date=eq.{day_iso}"
        "&select=region,tmax_c,tmean_c,rain_mm,solar_rad_mj_m2,wind_max_kmh",
        headers={"apikey": ANON, "Authorization": f"Bearer {ANON}"})
    rows = json.load(urllib.request.urlopen(req, timeout=60))
    df = pd.DataFrame(rows)
    for c in ("tmax_c", "tmean_c", "rain_mm", "solar_rad_mj_m2", "wind_max_kmh"):
        df[c] = pd.to_numeric(df[c], errors="coerce")
    return {
        "wx_tmax_c": df.tmax_c.mean(), "wx_tmean_c": df.tmean_c.mean(),
        "wx_rain_mm": df.rain_mm.mean(), "wx_solar_mj": df.solar_rad_mj_m2.mean(),
        "wx_wind_kmh": df.wind_max_kmh.mean(),
        "wx_nr_tmax_c": df[df.region == "NR"].tmax_c.mean(),
    }


def build_matrix():
    df = load_features()
    last = df.report_date.max()
    for t in TARGETS:  # ensure a feature row exists for every target day
        td = pd.Timestamp(t)
        if td > last:
            row = {c: None for c in df.columns}
            row.update({"report_date": td, **national_weather(t)})
            df = pd.concat([df, pd.DataFrame([row])], ignore_index=True)
    for c in df.columns:
        if c != "report_date":
            df[c] = pd.to_numeric(df[c], errors="coerce")
    return engineer(df.sort_values("report_date").reset_index(drop=True))


def gbm_predict(X, y, target, kind):
    tr = (X.index < target) & y.notna()
    te = X.index == target
    cap = float(X.loc[te, "price_cap"].iloc[0])
    if kind == "lightgbm":
        import lightgbm as lgb
        m = lgb.LGBMRegressor(**PARAMS)
    elif kind == "xgboost":
        from xgboost import XGBRegressor
        m = XGBRegressor(n_estimators=700, learning_rate=0.04, max_depth=8,
                         subsample=0.9, colsample_bytree=0.85, reg_lambda=1.0,
                         objective="reg:absoluteerror", verbosity=0)
    else:
        from catboost import CatBoostRegressor
        m = CatBoostRegressor(iterations=700, learning_rate=0.04, depth=8,
                              loss_function="MAE", verbose=False)
    m.fit(X[tr].astype(float), y[tr])
    return float(np.clip(m.predict(X[te].astype(float))[0], 0, cap))


def stats_predict(y, target):
    """AutoARIMA / AutoETS / SeasonalNaive via Nixtla statsforecast, h=1."""
    from statsforecast import StatsForecast
    from statsforecast.models import AutoARIMA, AutoETS, SeasonalNaive
    hist = y[(y.index < target) & y.notna()].tail(STAT_WINDOW_DAYS)
    df = pd.DataFrame({"unique_id": "dam", "ds": hist.index, "y": hist.values})
    sf = StatsForecast(models=[AutoARIMA(season_length=7),
                               AutoETS(season_length=7),
                               SeasonalNaive(season_length=7)], freq="D")
    fc = sf.forecast(df=df, h=1)
    return {"AutoARIMA": float(fc["AutoARIMA"].iloc[0]),
            "AutoETS": float(fc["AutoETS"].iloc[0]),
            "SeasonalNaive": float(fc["SeasonalNaive"].iloc[0])}


def prophet_predict(y, target):
    from prophet import Prophet
    hist = y[(y.index < target) & y.notna()].tail(STAT_WINDOW_DAYS)
    df = pd.DataFrame({"ds": hist.index, "y": hist.values})
    m = Prophet(weekly_seasonality=True, yearly_seasonality=True,
                daily_seasonality=False)
    m.add_country_holidays(country_name="IN")
    m.fit(df)
    future = pd.DataFrame({"ds": [pd.Timestamp(target)]})
    return float(m.predict(future)["yhat"].iloc[0])


def main():
    X, y = build_matrix()
    results = {}
    for t in TARGETS:
        td = pd.Timestamp(t)
        row = {}
        for kind in ("lightgbm", "xgboost", "catboost"):
            row[kind] = gbm_predict(X, y, td, kind)
        row.update(stats_predict(y, td))
        row["Prophet"] = prophet_predict(y, td)
        results[t] = row
        print(f"done {t}", flush=True)

    models = ["lightgbm", "xgboost", "catboost", "AutoARIMA", "AutoETS",
              "SeasonalNaive", "Prophet"]
    print("\n=== BAKE-OFF: predicted daily-avg DAM MCP (Rs/MWh) ===")
    hdr = f"{'model':<14}" + "".join(f"{t[5:]:>16}" for t in TARGETS)
    print(hdr + f"{'avg err(23,24)':>16}")
    for m in models:
        cells, errs = "", []
        for t in TARGETS:
            p = results[t][m]
            a = ACTUALS[t]
            if a:
                e = abs(p - a) / a * 100
                errs.append(e)
                cells += f"{p:>9.0f} ({e:>4.1f}%)"
            else:
                cells += f"{p:>15.0f} "
        print(f"{m:<14}{cells}{np.mean(errs):>15.1f}%")
    print(f"{'ACTUAL':<14}{ACTUALS[TARGETS[0]]:>9.0f}        {ACTUALS[TARGETS[1]]:>9.0f}        {'?':>15}")


if __name__ == "__main__":
    main()
