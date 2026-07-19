"""Bulk-load NRLDC jsonl into Supabase via PostgREST upsert (idempotent)."""
import json
import sys
import time
import requests

PROJECT_URL = "https://ltzulzadxqpwvfksmcfa.supabase.co"
API_KEY = sys.argv[1]
BATCH = 2000

TABLES = {
    "regional": ("nrldc_regional_availability", "report_date"),
    "state_demand": ("nrldc_state_demand", "report_date,state_canonical"),
    "state_generation": ("nrldc_state_generation", "report_date,state_canonical,seq"),
}

session = requests.Session()
session.headers.update({
    "apikey": API_KEY, "Authorization": f"Bearer {API_KEY}",
    "Content-Type": "application/json",
    "Prefer": "resolution=merge-duplicates,return=minimal",
})


def load(key, table, on_conflict):
    rows = [json.loads(l) for l in open(f"nrldc_{key}.jsonl")]
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
    print(f"{table}: {total} rows loaded")
    return total, True


for key, (table, oc) in TABLES.items():
    load(key, table, oc)
