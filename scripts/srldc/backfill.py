"""
Downloads every SRLDC Daily PSP Report over the modern-format range
(2018-01-01 -> today) and parses tables 1 / 2C / 3A into jsonl for Supabase.

SRLDC has no directory listing or API -- reports live at predictable date-based
URLs: /var/ftp/reports/psp/{YYYY}/{MonYY}/{DD-MM-YYYY}-psp.pdf. We generate one
URL per calendar day; missing days 404 (skipped), and any stray old-format PDF
is skipped by the parser's is_modern() check. Process pool for parallelism.
"""
import io
import json
import sys
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import date, timedelta, datetime

import requests
import pdfplumber

sys.path.insert(0, ".")
from parser import parse_report

BASE = "https://www.srldc.in/var/ftp/reports/psp"
TABLES = ["regional", "state_demand", "state_generation"]
START = date(2018, 1, 1)
END = date(2026, 7, 14)


def url_for(d):
    return f"{BASE}/{d.year}/{d.strftime('%b%y')}/{d.strftime('%d-%m-%Y')}-psp.pdf"


def worker(iso):
    requests.packages.urllib3.disable_warnings()
    s = requests.Session()
    s.verify = False
    d = date.fromisoformat(iso)
    try:
        r = s.get(url_for(d), timeout=60)
        if r.status_code != 200 or not r.content or len(r.content) < 3000:
            return ("miss", iso, r.status_code)
        with pdfplumber.open(io.BytesIO(r.content)) as pdf:
            data = parse_report(pdf)
        if data is None:
            return ("skip", iso, "old-format")
        return ("ok", iso, data)
    except Exception as e:
        return ("fail", iso, str(e)[:120])


def log(msg):
    line = f"[{datetime.now().isoformat(timespec='seconds')}] {msg}"
    print(line, flush=True)
    with open("backfill.log", "a") as f:
        f.write(line + "\n")


def main():
    days = []
    d = START
    while d <= END:
        days.append(d.isoformat())
        d += timedelta(days=1)
    for t in TABLES:
        open(f"srldc_{t}.jsonl", "w").close()
    log(f"starting: {len(days)} candidate days")
    done = miss = skip = 0
    failed = []
    buffers = {t: [] for t in TABLES}

    def flush():
        for t in TABLES:
            if buffers[t]:
                with open(f"srldc_{t}.jsonl", "a") as f:
                    for r in buffers[t]:
                        f.write(json.dumps(r) + "\n")
                buffers[t] = []

    with ProcessPoolExecutor(max_workers=8) as pool:
        futures = [pool.submit(worker, iso) for iso in days]
        for fut in as_completed(futures):
            status, iso, b = fut.result()
            if status == "miss":
                miss += 1
                continue
            if status == "skip":
                skip += 1
                continue
            if status == "fail":
                failed.append((iso, b))
                continue
            data = b
            done += 1
            if data["regional"]:
                buffers["regional"].append({**data["regional"], "report_date": iso})
            for s in data["state_demand"]:
                buffers["state_demand"].append({**s, "report_date": iso})
            for g in data["state_generation"]:
                buffers["state_generation"].append({**g, "report_date": iso})
            if done % 200 == 0:
                flush()
                log(f"  progress: {done} ok, {miss} miss, {skip} skip, {len(failed)} fail")
    flush()
    log(f"DONE. ok={done} miss={miss} skip={skip} fail={len(failed)}")
    if failed:
        json.dump(failed, open("srldc_failed.json", "w"))


if __name__ == "__main__":
    main()
