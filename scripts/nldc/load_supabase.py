"""
Bulk-loads the parsed NLDC jsonl files into Supabase via the PostgREST API.
Uses upsert (on_conflict + merge-duplicates) so it is idempotent and safe to
re-run: any duplicate lines produced across resume boundaries collapse onto
the same primary key rather than erroring or double-counting.
"""
import json
import sys
import time

import requests

PROJECT_URL = "https://ltzulzadxqpwvfksmcfa.supabase.co"
API_KEY = sys.argv[1]  # anon key passed on the command line
BATCH = 2000

TABLES = {
    "regional": ("nldc_regional_psp", "report_date,region"),
    "state": ("nldc_state_psp", "report_date,state_canonical"),
    "gen_outage": ("nldc_generation_outage", "report_date,region"),
    "sourcewise_gen": ("nldc_sourcewise_generation", "report_date,region"),
    "solar_nonsolar": ("nldc_solar_nonsolar_peak", "report_date,hour_type"),
}

session = requests.Session()
session.headers.update({
    "apikey": API_KEY,
    "Authorization": f"Bearer {API_KEY}",
    "Content-Type": "application/json",
    "Prefer": "resolution=merge-duplicates,return=minimal",
})


def load_file(key, table, on_conflict):
    rows = []
    with open(f"nldc_{key}.jsonl") as f:
        for line in f:
            rows.append(json.loads(line))
    url = f"{PROJECT_URL}/rest/v1/{table}?on_conflict={on_conflict}"
    total = 0
    for i in range(0, len(rows), BATCH):
        chunk = rows[i:i + BATCH]
        for attempt in range(3):
            r = session.post(url, data=json.dumps(chunk), timeout=120)
            if r.status_code in (200, 201, 204):
                break
            if attempt == 2:
                print(f"  FAIL {table} batch {i}: {r.status_code} {r.text[:300]}")
                return total, False
            time.sleep(2 * (attempt + 1))
        total += len(chunk)
        print(f"  {table}: {total}/{len(rows)}")
    return total, True


def main():
    for key, (table, on_conflict) in TABLES.items():
        print(f"=== loading {table} ===")
        total, ok = load_file(key, table, on_conflict)
        print(f"{table}: {total} rows loaded, ok={ok}")


if __name__ == "__main__":
    main()
