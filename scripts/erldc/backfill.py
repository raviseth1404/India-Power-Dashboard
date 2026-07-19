"""
Downloads every modern-era ERLDC Daily PSP Report (2020-04-11+), parses tables
1 / 2C / 3A, writes jsonl for Supabase. Report date comes from the list's
fileDate epoch (authoritative, matches the data date). Old POSOCO-format reports
are skipped automatically (parser returns None). Process pool for parallelism.
"""
import io
import json
import sys
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import datetime

import requests
import pdfplumber

sys.path.insert(0, ".")
from parser import parse_report

DL = "https://erldc.in/api//downloadFile/DailyPSPReport/"
TABLES = ["regional", "state_demand", "state_generation"]


def worker(cand):
    requests.packages.urllib3.disable_warnings()
    s = requests.Session()
    s.verify = False
    rd = cand["report_date"]
    try:
        content = s.get(DL + cand["id"], timeout=60).content
        if not content or len(content) < 1000:
            return ("fail", rd, "empty")
        with pdfplumber.open(io.BytesIO(content)) as pdf:
            data = parse_report(pdf)
        if data is None:
            return ("skip", rd, "old-format")
        return ("ok", rd, data)
    except Exception as e:
        return ("fail", rd, str(e)[:120])


def log(msg):
    line = f"[{datetime.now().isoformat(timespec='seconds')}] {msg}"
    print(line, flush=True)
    with open("backfill.log", "a") as f:
        f.write(line + "\n")


def main():
    cands = json.load(open("/tmp/erldc_cands.json"))
    for t in TABLES:
        open(f"erldc_{t}.jsonl", "w").close()
    log(f"starting: {len(cands)} candidates")
    done = skipped = 0
    failed = []
    buffers = {t: [] for t in TABLES}

    def flush():
        for t in TABLES:
            if buffers[t]:
                with open(f"erldc_{t}.jsonl", "a") as f:
                    for r in buffers[t]:
                        f.write(json.dumps(r) + "\n")
                buffers[t] = []

    with ProcessPoolExecutor(max_workers=8) as pool:
        futures = [pool.submit(worker, c) for c in cands]
        for fut in as_completed(futures):
            status, rd, b = fut.result()
            if status == "fail":
                failed.append((rd, b))
                continue
            if status == "skip":
                skipped += 1
                continue
            data = b
            done += 1
            if data["regional"]:
                buffers["regional"].append({**data["regional"], "report_date": rd})
            for s in data["state_demand"]:
                buffers["state_demand"].append({**s, "report_date": rd})
            for g in data["state_generation"]:
                buffers["state_generation"].append({**g, "report_date": rd})
            if done % 100 == 0:
                flush()
                log(f"  progress: {done} ok, {skipped} skipped, {len(failed)} failed")
    flush()
    log(f"DONE. ok={done} skipped={skipped} failed={len(failed)}")
    if failed:
        json.dump(failed, open("erldc_failed.json", "w"))


if __name__ == "__main__":
    main()
