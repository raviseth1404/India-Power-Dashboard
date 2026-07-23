"""
Score a delivery day's forecasts against the discovered DAM prices —
separately for every stored model tag (ensemble, LightGBM shadow, etc.),
at both the daily-average and 96-block level.

If the delivery day's prices aren't in Supabase yet (they publish ~13:00 IST
the day before delivery), fetches them from IEX first and upserts (needs
SUPABASE_SERVICE_ROLE_KEY for that; scoring itself is read-only).

Usage: python3 score_day.py [YYYY-MM-DD]     (default: max forecast_date)
"""
import json
import os
import sys
import urllib.request

import numpy as np
import pandas as pd

from common import SUPABASE_URL, ANON


def rest(path, key=ANON):
    req = urllib.request.Request(f"{SUPABASE_URL}/rest/v1/{path}",
        headers={"apikey": key, "Authorization": f"Bearer {key}"})
    return json.load(urllib.request.urlopen(req, timeout=60))


def ensure_actuals(day):
    rows = rest(f"iex_dam?select=block,mcp_rs_mwh&report_date=eq.{day}&order=block.asc")
    if len(rows) >= 96:
        return rows
    key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
    if not key:
        sys.exit(f"actuals for {day} not in DB and no SUPABASE_SERVICE_ROLE_KEY to fetch")
    print(f"fetching {day} from IEX…", flush=True)
    import importlib.util
    here = os.path.dirname(os.path.abspath(__file__))
    spec = importlib.util.spec_from_file_location(
        "du", os.path.join(here, "..", "cron", "daily_update.py"))
    du = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(du)
    from datetime import date
    du.do_iex([date.fromisoformat(day)])
    du.refresh_matview()
    rows = rest(f"iex_dam?select=block,mcp_rs_mwh&report_date=eq.{day}&order=block.asc")
    if len(rows) < 96:
        sys.exit(f"IEX has not published {day} yet")
    return rows


def main():
    if len(sys.argv) > 1:
        day = sys.argv[1]
    else:
        day = rest("dam_forecast?select=forecast_date&order=forecast_date.desc&limit=1")[0]["forecast_date"]

    act = pd.DataFrame(ensure_actuals(day))
    act["mcp_rs_mwh"] = pd.to_numeric(act["mcp_rs_mwh"])
    actual_avg = act["mcp_rs_mwh"].mean()

    daily = rest(f"dam_forecast?select=model,p50,p10,p90&forecast_date=eq.{day}&order=model.asc")
    blocks = rest(f"dam_block_forecast?select=model,block,p50&forecast_date=eq.{day}&order=model.asc,block.asc")
    bdf = pd.DataFrame(blocks) if blocks else pd.DataFrame(columns=["model", "block", "p50"])
    if len(bdf):
        bdf["p50"] = pd.to_numeric(bdf["p50"])

    print(f"\n=== SCORECARD — delivery {day} ===")
    print(f"actual daily avg: ₹{actual_avg:.0f}  (peak block ₹{act.mcp_rs_mwh.max():.0f}, "
          f"min ₹{act.mcp_rs_mwh.min():.0f})\n")
    print(f"{'model':<12}{'daily P50':>10}{'err':>9}{'err%':>7}{'in band':>9}"
          f"{'| block MAPE':>13}{'block MAE':>11}{'peak err%':>11}")
    for d in daily:
        p50, p10, p90 = float(d["p50"]), float(d["p10"] or 0), float(d["p90"] or 0)
        err = p50 - actual_avg
        inband = "yes" if p10 <= actual_avg <= p90 else "NO"
        line = (f"{d['model']:<12}{p50:>10.0f}{err:>+9.0f}{abs(err)/actual_avg*100:>6.1f}%"
                f"{inband:>9}")
        mb = bdf[bdf.model == d["model"]]
        if len(mb) == 0 and len(bdf):
            # derive block curve for models without stored blocks by scaling the
            # stored shape (shape is model-independent) to this model's level
            ref = bdf[bdf.model == bdf.model.iloc[0]]
            mb = ref.assign(p50=ref.p50 * (p50 / float(ref.p50.mean())))
        if len(mb) == 96:
            merged = mb.sort_values("block").reset_index(drop=True)
            a = act.sort_values("block").mcp_rs_mwh.reset_index(drop=True)
            mape = float(np.mean(np.abs(merged.p50 - a) / a) * 100)
            mae = float(np.mean(np.abs(merged.p50 - a)))
            peak_err = (float(merged.p50.max()) - float(a.max())) / float(a.max()) * 100
            line += f"{mape:>12.1f}%{mae:>11.0f}{peak_err:>+10.1f}%"
        print(line)
    print("\n(block rows derived by scaling the shared shape where a model has no stored blocks)")


if __name__ == "__main__":
    main()
