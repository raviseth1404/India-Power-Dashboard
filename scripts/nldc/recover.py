"""
Recovery pass for the dates that failed in the main backfill. For each failed
date we re-list its month and try EVERY file entry for that date (not just the
preferred Excel), so a broken/0-byte Excel can fall back to the day's PDF, and
a broken upload.pdf can fall back to any correctly-named duplicate. Anything
still unrecoverable is a genuine gap in grid-india's own archive.
"""
import io
import json
import sys

import requests
import pdfplumber

sys.path.insert(0, ".")
from parser import parse_pdf_text, parse_excel

API_BASE = "https://webapi.grid-india.in/api/v1"
CDN_BASE = "https://webcdn.grid-india.in"
HEADERS = {"Content-Type": "application/json", "Referer": "https://grid-india.in/"}
FY_FOR = lambda d: f"{d.year if d.month >= 4 else d.year - 1}-{str((d.year if d.month >= 4 else d.year - 1) + 1)[2:]}"

session = requests.Session()
session.headers.update(HEADERS)
session.verify = False
requests.packages.urllib3.disable_warnings()

from datetime import date

TABLE_NAMES = ["regional", "state", "gen_outage", "sourcewise_gen", "solar_nonsolar"]


def list_files(fy, month):
    body = {"_source": "GRDW", "_type": "DAILY_PSP_REPORT", "_fileDate": fy, "_month": month}
    r = session.post(f"{API_BASE}/file", json=body, timeout=30)
    r.raise_for_status()
    return r.json().get("retData") or []


def try_parse(entry):
    url = f"{CDN_BASE}/{entry['FilePath']}"
    try:
        content = session.get(url, timeout=45).content
        if not content or len(content) < 500:
            return None
        if "excel" in entry["MimeType"] or entry["FilePath"].endswith(".xls"):
            return parse_excel(file_contents=content), "excel"
        with pdfplumber.open(io.BytesIO(content)) as pdf:
            text = "\n".join(p.extract_text() or "" for p in pdf.pages)
        return parse_pdf_text(text), "pdf"
    except Exception:
        return None


def main():
    failed = json.load(open("nldc_failed_dates.json"))
    recovered = {t: [] for t in TABLE_NAMES}
    still_failed = []
    # group failed dates by (fy, month) to minimize API calls
    months = {}
    for ds in failed:
        d = date.fromisoformat(ds)
        key = (FY_FOR(d), f"{d.month:02d}")
        months.setdefault(key, []).append(ds)

    for (fy, month), dates in months.items():
        entries = list_files(fy, month)
        for ds in dates:
            day_entries = [e for e in entries if e.get("Field2") == ds]
            got = None
            for e in day_entries:
                res = try_parse(e)
                if res is not None:
                    got = res
                    break
            if got is None:
                still_failed.append(ds)
                print(f"  STILL FAILED {ds} ({len(day_entries)} entries, all broken)")
                continue
            tables, fmt = got
            for t in TABLE_NAMES:
                for row in tables[t]:
                    row["report_date"] = ds
                    row["source_format"] = fmt
                    recovered[t].append(row)
            print(f"  RECOVERED {ds} via {fmt}")

    for t in TABLE_NAMES:
        if recovered[t]:
            with open(f"nldc_{t}_recovered.jsonl", "w") as f:
                for row in recovered[t]:
                    f.write(json.dumps(row) + "\n")
    json.dump(still_failed, open("nldc_still_failed.json", "w"))
    print(f"\nRECOVERED {len(failed) - len(still_failed)} / {len(failed)} dates; "
          f"{len(still_failed)} genuinely unrecoverable")


if __name__ == "__main__":
    main()
