"""
Production daily DAM forecast. Runs on the VM right after the daily data
update (the 12:30 IST cron):

  1. Top up weather_daily: recent days the ERA5 archive hasn't reached yet,
     PLUS tomorrow, from the Open-Meteo forecast API (per city, past_days=7).
  2. Rebuild features; append tomorrow's row (weather + calendar known,
     fundamentals become lags of today/yesterday).
  3. Train LightGBM (P50 + P10/P90 quantiles) on ALL completed history.
  4. Upsert tomorrow's prediction into dam_forecast.

Needs SUPABASE_SERVICE_ROLE_KEY in the environment (writes).
"""
import json
import os
import time
from datetime import date, timedelta, datetime, timezone

import lightgbm as lgb
import numpy as np
import pandas as pd
import requests

from common import SUPABASE_URL, PARAMS, load_features, engineer

SERVICE_KEY = os.environ["SUPABASE_SERVICE_ROLE_KEY"]
# Tag distinguishes runs (e.g. ens-0830 vs ens-1230 A/B) — one row per (date, tag).
MODEL_TAG = os.environ.get("MODEL_TAG", "ens-v1")
IST = timezone(timedelta(hours=5, minutes=30))

CITIES = [
    ("Delhi", "Delhi", "NR", 28.61, 77.21),
    ("Lucknow", "Uttar Pradesh", "NR", 26.85, 80.95),
    ("Jaipur", "Rajasthan", "NR", 26.91, 75.79),
    ("Ludhiana", "Punjab", "NR", 30.90, 75.85),
    ("Hisar", "Haryana", "NR", 29.15, 75.72),
    ("Mumbai", "Maharashtra", "WR", 19.08, 72.88),
    ("Nagpur", "Maharashtra", "WR", 21.15, 79.09),
    ("Ahmedabad", "Gujarat", "WR", 23.03, 72.58),
    ("Bhopal", "Madhya Pradesh", "WR", 23.26, 77.41),
    ("Raipur", "Chhattisgarh", "WR", 21.25, 81.63),
    ("Chennai", "Tamil Nadu", "SR", 13.08, 80.27),
    ("Bengaluru", "Karnataka", "SR", 12.97, 77.59),
    ("Hyderabad", "Telangana", "SR", 17.39, 78.49),
    ("Vijayawada", "Andhra Pradesh", "SR", 16.51, 80.65),
    ("Kochi", "Kerala", "SR", 9.93, 76.27),
    ("Kolkata", "West Bengal", "ER", 22.57, 88.36),
    ("Patna", "Bihar", "ER", 25.59, 85.14),
    ("Bhubaneswar", "Odisha", "ER", 20.30, 85.82),
    ("Guwahati", "Assam", "NER", 26.14, 91.74),
]
DAILY_VARS = ("temperature_2m_max,temperature_2m_min,temperature_2m_mean,"
              "precipitation_sum,shortwave_radiation_sum,wind_speed_10m_max")

sess = requests.Session()
sess.headers.update({
    "apikey": SERVICE_KEY, "Authorization": f"Bearer {SERVICE_KEY}",
    "Content-Type": "application/json",
    "Prefer": "resolution=merge-duplicates,return=minimal",
})


def log(msg):
    print(f"[{datetime.now(IST).isoformat(timespec='seconds')}] {msg}", flush=True)


def topup_weather():
    """Fill the archive lag + tomorrow using the forecast API (past_days=7)."""
    rows = []
    for city, state, region, lat, lon in CITIES:
        url = ("https://api.open-meteo.com/v1/forecast"
               f"?latitude={lat}&longitude={lon}&daily={DAILY_VARS}"
               "&past_days=7&forecast_days=3&timezone=Asia%2FKolkata")
        for attempt in range(4):
            r = requests.get(url, timeout=60)
            if r.status_code == 200:
                break
            time.sleep(10 * (attempt + 1))
        d = r.json()["daily"]
        for i, day in enumerate(d["time"]):
            rows.append({
                "report_date": day, "city": city, "state": state, "region": region,
                "tmax_c": d["temperature_2m_max"][i],
                "tmin_c": d["temperature_2m_min"][i],
                "tmean_c": d["temperature_2m_mean"][i],
                "rain_mm": d["precipitation_sum"][i],
                "solar_rad_mj_m2": d["shortwave_radiation_sum"][i],
                "wind_max_kmh": d["wind_speed_10m_max"][i],
            })
        time.sleep(0.5)
    r = sess.post(f"{SUPABASE_URL}/rest/v1/weather_daily?on_conflict=report_date,city",
                  data=json.dumps(rows), timeout=120)
    log(f"weather top-up: {len(rows)} rows, HTTP {r.status_code}")


def national_weather(day_iso):
    """National + NR aggregates for one day, straight from weather_daily."""
    r = sess.get(f"{SUPABASE_URL}/rest/v1/weather_daily?report_date=eq.{day_iso}"
                 "&select=region,tmax_c,tmean_c,rain_mm,solar_rad_mj_m2,wind_max_kmh",
                 timeout=60)
    rows = r.json()
    if not rows:
        return None
    df = pd.DataFrame(rows)
    for c in ("tmax_c", "tmean_c", "rain_mm", "solar_rad_mj_m2", "wind_max_kmh"):
        df[c] = pd.to_numeric(df[c], errors="coerce")
    return {
        "wx_tmax_c": df.tmax_c.mean(), "wx_tmean_c": df.tmean_c.mean(),
        "wx_rain_mm": df.rain_mm.mean(), "wx_solar_mj": df.solar_rad_mj_m2.mean(),
        "wx_wind_kmh": df.wind_max_kmh.mean(),
        "wx_nr_tmax_c": df[df.region == "NR"].tmax_c.mean(),
    }


def main():
    topup_weather()

    df = load_features()
    target_day = df.report_date.max() + pd.Timedelta(days=1)
    log(f"history through {df.report_date.max().date()}; forecasting {target_day.date()}")

    wx = national_weather(target_day.date().isoformat())
    if wx is None:
        log("no weather for target day — aborting")
        return
    tomorrow = {c: None for c in df.columns}
    tomorrow.update({"report_date": target_day, **wx})
    df = pd.concat([df, pd.DataFrame([tomorrow])], ignore_index=True)
    for c in df.columns:
        if c != "report_date":
            df[c] = pd.to_numeric(df[c], errors="coerce")

    X, y = engineer(df)
    train = y.notna()
    te = X.index == target_day
    cap = float(X.loc[te, "price_cap"].iloc[0])

    # --- 3-model ensemble P50 (validated: 8.1% vs 8.2% MAPE over 1,301 days) ---
    out = {}
    for name, extra in [("lgb", {}), ("p10", {"objective": "quantile", "alpha": 0.1}),
                        ("p90", {"objective": "quantile", "alpha": 0.9})]:
        m = lgb.LGBMRegressor(**{**PARAMS, **extra})
        m.fit(X[train], y[train])
        out[name] = float(m.predict(X[te])[0])

    from xgboost import XGBRegressor
    xgm = XGBRegressor(n_estimators=700, learning_rate=0.04, max_depth=8,
                       subsample=0.9, colsample_bytree=0.85, reg_lambda=1.0,
                       objective="reg:absoluteerror", verbosity=0)
    xgm.fit(X[train].astype(float), y[train])
    out["xgb"] = float(xgm.predict(X[te].astype(float))[0])

    from statsforecast import StatsForecast
    from statsforecast.models import AutoETS
    hist = y[train].sort_index().tail(730)
    sdf = pd.DataFrame({"unique_id": "dam", "ds": hist.index, "y": hist.values})
    h = (target_day - hist.index.max()).days
    sf = StatsForecast(models=[AutoETS(season_length=7)], freq="D")
    out["ets"] = float(sf.forecast(df=sdf, h=h)["AutoETS"].iloc[-1])

    ens = float(np.mean([out["lgb"], out["xgb"], out["ets"]]))
    log(f"members: lgb ₹{out['lgb']:.0f} xgb ₹{out['xgb']:.0f} ets ₹{out['ets']:.0f}")

    # Shadow row: keep the standalone LightGBM forecast scored alongside the
    # ensemble (tag ens-0830 -> lgb-0830) so the two stay comparable daily.
    shadow = {"forecast_date": target_day.date().isoformat(),
              "p50": round(float(np.clip(out["lgb"], 0, cap)), 2),
              "p10": round(float(np.clip(out["p10"], 0, cap)), 2),
              "p90": round(float(np.clip(out["p90"], 0, cap)), 2),
              "model": MODEL_TAG.replace("ens", "lgb"),
              "generated_at": datetime.now(timezone.utc).isoformat()}
    sess.post(f"{SUPABASE_URL}/rest/v1/dam_forecast?on_conflict=forecast_date,model",
              data=json.dumps([shadow]), timeout=60)

    shift = ens - out["lgb"]  # re-centre LGB's quantile band on the ensemble
    out = {"p50": float(np.clip(ens, 0, cap)),
           "p10": float(np.clip(out["p10"] + shift, 0, cap)),
           "p90": float(np.clip(out["p90"] + shift, 0, cap))}

    rec = {"forecast_date": target_day.date().isoformat(),
           "p50": round(out["p50"], 2), "p10": round(out["p10"], 2),
           "p90": round(out["p90"], 2), "model": MODEL_TAG,
           "generated_at": datetime.now(timezone.utc).isoformat()}
    r = sess.post(f"{SUPABASE_URL}/rest/v1/dam_forecast?on_conflict=forecast_date,model",
                  data=json.dumps([rec]), timeout=60)
    log(f"forecast {rec['forecast_date']}: P50 ₹{rec['p50']:.0f} "
        f"(P10 {rec['p10']:.0f} – P90 {rec['p90']:.0f}) HTTP {r.status_code}")

    block_forecast(target_day, out["p50"], cap)


def block_forecast(target_day, daily_p50, cap):
    """96-block curve = daily P50 × recent intraday shape (day-type aware
    mean of the last 28 days' normalized shapes; blocks average back to P50).
    Shape-only error ≈ 23% block MAPE — blocks are indicative, the daily
    level is the calibrated number."""
    since = (target_day - pd.Timedelta(days=29)).date().isoformat()
    rows, off = [], 0
    while True:
        r = sess.get(f"{SUPABASE_URL}/rest/v1/iex_dam?select=report_date,block,mcp_rs_mwh"
                     f"&report_date=gte.{since}&order=report_date.asc,block.asc"
                     f"&offset={off}&limit=1000", timeout=60)
        chunk = r.json()
        rows += chunk
        if len(chunk) < 1000:
            break
        off += 1000
    df = pd.DataFrame(rows)
    df["mcp_rs_mwh"] = pd.to_numeric(df["mcp_rs_mwh"], errors="coerce")
    piv = df.pivot(index="report_date", columns="block", values="mcp_rs_mwh").dropna()
    shape = piv.div(piv.mean(axis=1), axis=0)
    dtypes_we = pd.to_datetime(shape.index).dayofweek >= 5
    target_we = target_day.dayofweek >= 5
    sel = shape[dtypes_we == target_we]
    est = (sel if len(sel) >= 3 else shape).mean()
    est = est / est.mean()
    blocks = np.clip(est.values * daily_p50, 0, cap)
    recs = [{"forecast_date": target_day.date().isoformat(), "block": int(b),
             "model": MODEL_TAG, "p50": round(float(v), 2),
             "generated_at": datetime.now(timezone.utc).isoformat()}
            for b, v in zip(est.index, blocks)]
    r = sess.post(f"{SUPABASE_URL}/rest/v1/dam_block_forecast?on_conflict=forecast_date,block,model",
                  data=json.dumps(recs), timeout=60)
    log(f"block curve: {len(recs)} blocks, peak ₹{blocks.max():.0f} "
        f"min ₹{blocks.min():.0f} HTTP {r.status_code}")


if __name__ == "__main__":
    main()
