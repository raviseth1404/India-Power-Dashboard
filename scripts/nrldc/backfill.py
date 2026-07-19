"""
Downloads every NRLDC Daily PSP Report (781 listed), parses tables 1 / 2C / 3A,
writes them to jsonl for Supabase loading. Report date comes from the PDF body
("...For DD-Mon-YYYY"), which is authoritative and immune to messy titles.

Uses a process pool (not threads): parsing is CPU-bound pdfplumber work and the
GIL makes threads useless for it, so real parallelism across cores is needed.
"""
import io
import json
import re
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import datetime, date

import requests
import pdfplumber

sys.path.insert(0, ".")
from parser import parse_report

DL_BASE = "https://nrldc.in/download-file?any="
MONTHS = {m: i for i, m in enumerate(
    ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"], 1)}
TABLES = ["regional", "state_demand", "state_generation"]


def body_date(pdf):
    t = (pdf.pages[0].extract_text() or "").replace("\n", " ")
    m = re.search(r"For\s*(\d{2})-([A-Za-z]{3})-(\d{4})", t)
    if not m:
        return None
    dd, mon, yyyy = m.groups()
    if mon not in MONTHS:
        return None
    return date(int(yyyy), MONTHS[mon], int(dd)).isoformat()


def worker(path):
    """Runs in a separate process: download + parse one report."""
    requests.packages.urllib3.disable_warnings()
    s = requests.Session()
    s.verify = False
    for attempt in range(3):
        try:
            content = s.get(DL_BASE + path, timeout=60).content
            if not content or len(content) < 1000:
                return ("fail", path, "empty")
            with pdfplumber.open(io.BytesIO(content)) as pdf:
                rd = body_date(pdf)
                if rd is None:
                    return ("fail", path, "no-date")
                data = parse_report(pdf)
            return ("ok", rd, data)
        except Exception as e:
            if attempt == 2:
                return ("fail", path, str(e)[:120])
            time.sleep(1.0 * (attempt + 1))


def log(msg):
    line = f"[{datetime.now().isoformat(timespec='seconds')}] {msg}"
    print(line, flush=True)
    with open("backfill.log", "a") as f:
        f.write(line + "\n")


def main():
    paths = json.load(open("/tmp/nrldc_paths.json"))
    # fresh run
    for t in TABLES:
        open(f"nrldc_{t}.jsonl", "w").close()
    log(f"starting: {len(paths)} paths, process pool")

    seen = set()
    done = 0
    failed = []
    buffers = {t: [] for t in TABLES}

    def flush():
        for t in TABLES:
            if buffers[t]:
                with open(f"nrldc_{t}.jsonl", "a") as f:
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
            for srow in data["state_demand"]:
                buffers["state_demand"].append({**srow, "report_date": rd})
            for g in data["state_generation"]:
                buffers["state_generation"].append({**g, "report_date": rd})
            if done % 50 == 0:
                flush()
                log(f"  progress: {done} reports, failed={len(failed)}")
    flush()
    log(f"DONE. reports={done} failed={len(failed)}")
    if failed:
        json.dump(failed, open("nrldc_failed.json", "w"))


if __name__ == "__main__":
    main()
