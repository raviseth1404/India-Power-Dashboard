"""
Downloads every WRLDC Daily PSP Report (2019+), parses tables 1 / 2C / 3A,
writes jsonl for Supabase. Report date comes from the filename
(WRLDC_PSP_Report_DD-MM-YYYY.pdf). Process pool for real parallelism.
"""
import io
import json
import re
import sys
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import datetime, date

import requests
import pdfplumber

sys.path.insert(0, ".")
from parser import parse_report

HOST = "https://reporting.wrldc.in:8081"
TABLES = ["regional", "state_demand", "state_generation"]


def date_from_path(path):
    m = re.search(r"(\d{2})-(\d{2})-(\d{4})\.pdf$", path)
    if not m:
        return None
    dd, mm, yyyy = m.groups()
    try:
        return date(int(yyyy), int(mm), int(dd)).isoformat()
    except ValueError:
        return None


def worker(path):
    requests.packages.urllib3.disable_warnings()
    s = requests.Session()
    s.verify = False
    rd = date_from_path(path)
    if rd is None:
        return ("fail", path, "bad-date")
    try:
        content = s.get(HOST + path, timeout=60).content
        if not content or len(content) < 1000:
            return ("fail", path, "empty")
        with pdfplumber.open(io.BytesIO(content)) as pdf:
            data = parse_report(pdf)
        return ("ok", rd, data)
    except Exception as e:
        return ("fail", path, str(e)[:120])


def log(msg):
    line = f"[{datetime.now().isoformat(timespec='seconds')}] {msg}"
    print(line, flush=True)
    with open("backfill.log", "a") as f:
        f.write(line + "\n")


def main():
    paths = json.load(open("/tmp/wrldc_urls.json"))
    for t in TABLES:
        open(f"wrldc_{t}.jsonl", "w").close()
    log(f"starting: {len(paths)} reports")
    seen = set()
    done = 0
    failed = []
    buffers = {t: [] for t in TABLES}

    def flush():
        for t in TABLES:
            if buffers[t]:
                with open(f"wrldc_{t}.jsonl", "a") as f:
                    for r in buffers[t]:
                        f.write(json.dumps(r) + "\n")
                buffers[t] = []

    with ProcessPoolExecutor(max_workers=8) as pool:
        futures = [pool.submit(worker, p) for p in paths]
        for fut in as_completed(futures):
            status, a, b = fut.result()
            if status == "fail":
                failed.append((a, b))
                continue
            rd, data = a, b
            if rd in seen:
                continue
            seen.add(rd)
            done += 1
            if data["regional"]:
                buffers["regional"].append({**data["regional"], "report_date": rd})
            for s in data["state_demand"]:
                buffers["state_demand"].append({**s, "report_date": rd})
            for g in data["state_generation"]:
                buffers["state_generation"].append({**g, "report_date": rd})
            if done % 100 == 0:
                flush()
                log(f"  progress: {done}, failed={len(failed)}")
    flush()
    log(f"DONE. reports={done} failed={len(failed)}")
    if failed:
        json.dump(failed, open("wrldc_failed.json", "w"))


if __name__ == "__main__":
    main()
