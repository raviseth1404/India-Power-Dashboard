"""
Corrects already-parsed jsonl in place, without re-downloading:
  - state:   recompute state_canonical from state_raw (splits DD / DNH)
  - regional & solar_nonsolar: normalize max_demand_time Excel serials -> HH:MM
Applies to both the main backfill files and the recovery files.
"""
import glob
import json
import sys

sys.path.insert(0, ".")
from parser import STATE_CANON, excel_time


def fix_state(path):
    out = []
    for line in open(path):
        r = json.loads(line)
        canon, region = STATE_CANON[r["state_raw"]]
        r["state_canonical"] = canon
        r["region"] = region
        out.append(r)
    with open(path, "w") as f:
        for r in out:
            f.write(json.dumps(r) + "\n")
    return len(out)


def fix_time(path):
    out = []
    for line in open(path):
        r = json.loads(line)
        r["max_demand_time"] = excel_time(r.get("max_demand_time"))
        out.append(r)
    with open(path, "w") as f:
        for r in out:
            f.write(json.dumps(r) + "\n")
    return len(out)


for p in glob.glob("nldc_state*.jsonl"):
    print("state fix", p, fix_state(p))
for pat in ("nldc_regional*.jsonl", "nldc_solar_nonsolar*.jsonl"):
    for p in glob.glob(pat):
        print("time fix", p, fix_time(p))
