"""
Fetches every available NLDC Daily PSP Report (2013-04-01 -> today), parses
it with parser.py, and writes the combined rows to local JSON files for
loading into Supabase. Downloads run with modest thread concurrency to be
polite to grid-india.in. Progress is logged to backfill.log as it goes so it
can be monitored while running as a background job.
"""
import io
import json
import re
import sys
import time
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

import requests

sys.path.insert(0, ".")
from parser import parse_pdf_text, parse_excel

import pdfplumber

API_BASE = "https://webapi.grid-india.in/api/v1"
CDN_BASE = "https://webcdn.grid-india.in"
HEADERS = {"Content-Type": "application/json", "Referer": "https://grid-india.in/"}

FISCAL_YEARS = [f"{y}-{str(y + 1)[2:]}" for y in range(2013, 2027)]  # 2013-14 .. 2026-27
MONTHS = [f"{m:02d}" for m in range(1, 13)]

session = requests.Session()
session.headers.update(HEADERS)
session.verify = False
requests.packages.urllib3.disable_warnings()

LOG = open("backfill.log", "a")


def log(msg):
    line = f"[{datetime.now().isoformat(timespec='seconds')}] {msg}"
    print(line)
    LOG.write(line + "\n")
    LOG.flush()


def list_files(fy, month, retries=3):
    body = {"_source": "GRDW", "_type": "DAILY_PSP_REPORT", "_fileDate": fy, "_month": month}
    for attempt in range(retries):
        try:
            r = session.post(f"{API_BASE}/file", json=body, timeout=30)
            r.raise_for_status()
            data = r.json()
            return data.get("retData") or []
        except Exception as e:
            if attempt == retries - 1:
                log(f"  ERROR listing {fy}/{month}: {e}")
                return []
            time.sleep(1.5 * (attempt + 1))


def download(path, retries=3):
    url = f"{CDN_BASE}/{path}"
    for attempt in range(retries):
        try:
            r = session.get(url, timeout=45)
            r.raise_for_status()
            return r.content
        except Exception as e:
            if attempt == retries - 1:
                log(f"  ERROR downloading {path}: {e}")
                return None
            time.sleep(1.5 * (attempt + 1))


def parse_one(entry):
    """Download + parse a single day's chosen file. Returns (date_iso, tables, fmt) or None."""
    path = entry["FilePath"]
    mime = entry["MimeType"]
    date_iso = entry["Field2"]
    content = download(path)
    if content is None:
        return None
    try:
        if "excel" in mime or path.endswith(".xls"):
            tables = parse_excel(file_contents=content)
            fmt = "excel"
        else:
            with pdfplumber.open(io.BytesIO(content)) as pdf:
                text = "\n".join(p.extract_text() or "" for p in pdf.pages)
            tables = parse_pdf_text(text)
            fmt = "pdf"
        return date_iso, tables, fmt
    except Exception as e:
        log(f"  PARSE ERROR {date_iso} ({path}): {e}\n{traceback.format_exc(limit=2)}")
        return None


TABLE_NAMES = ["regional", "state", "gen_outage", "sourcewise_gen", "solar_nonsolar"]


def load_seen_dates():
    """Resume support: dates already written to nldc_regional.jsonl are done."""
    seen = set()
    try:
        with open("nldc_regional.jsonl") as f:
            for line in f:
                seen.add(json.loads(line)["report_date"])
    except FileNotFoundError:
        pass
    return seen


def append_rows(table_name, rows):
    with open(f"nldc_{table_name}.jsonl", "a") as f:
        for row in rows:
            f.write(json.dumps(row) + "\n")


def main():
    seen_dates = load_seen_dates()
    total_days = len(seen_dates)
    failed_days = []
    log(f"resuming with {total_days} days already done")

    for fy in FISCAL_YEARS:
        for month in MONTHS:
            entries = list_files(fy, month)
            if not entries:
                continue
            by_date = {}
            for e in entries:
                d = e.get("Field2")
                if not d:
                    continue
                # prefer excel over pdf when both exist for the same date
                cur = by_date.get(d)
                is_excel = "excel" in e["MimeType"] or e["FilePath"].endswith(".xls")
                if cur is None or (is_excel and "excel" not in cur["MimeType"] and not cur["FilePath"].endswith(".xls")):
                    by_date[d] = e

            new_entries = [e for d, e in by_date.items() if d not in seen_dates]
            if not new_entries:
                continue

            batch = {t: [] for t in TABLE_NAMES}
            with ThreadPoolExecutor(max_workers=6) as pool:
                futures = {pool.submit(parse_one, e): e for e in new_entries}
                for fut in as_completed(futures):
                    e = futures[fut]
                    result = fut.result()
                    if result is None:
                        failed_days.append(e.get("Field2"))
                        continue
                    date_iso, tables, fmt = result
                    if date_iso in seen_dates:
                        continue
                    seen_dates.add(date_iso)
                    total_days += 1
                    for t in TABLE_NAMES:
                        for row in tables[t]:
                            row["report_date"] = date_iso
                            row["source_format"] = fmt
                            batch[t].append(row)

            # Flush to disk after every month so a kill/crash loses at most
            # one month's worth of work, not the whole run.
            for t in TABLE_NAMES:
                append_rows(t, batch[t])

            log(f"{fy} {month}: +{len(new_entries)} days processed, running total={total_days}, failed={len(failed_days)}")

    log(f"DONE. total_days={total_days} failed_days={len(failed_days)}")
    if failed_days:
        with open("nldc_failed_dates.json", "w") as f:
            json.dump(sorted(failed_days), f)
        log(f"failed dates written to nldc_failed_dates.json ({len(failed_days)})")


if __name__ == "__main__":
    main()
