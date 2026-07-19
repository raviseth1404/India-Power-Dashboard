"""Bulk-load IEX jsonl into Supabase via PostgREST upsert (idempotent).

Usage:  python load_supabase.py <ANON_KEY> [dam|rtm ...]  (default: both)
"""
import json
import sys
import time
import requests

PROJECT_URL = "https://ltzulzadxqpwvfksmcfa.supabase.co"
API_KEY = sys.argv[1]
MARKETS = sys.argv[2:] or ["dam", "rtm"]
BATCH = 4000

TABLE = {"dam": "iex_dam", "rtm": "iex_rtm"}
ON_CONFLICT = "report_date,block"

session = requests.Session()
session.headers.update({
    "apikey": API_KEY, "Authorization": f"Bearer {API_KEY}",
    "Content-Type": "application/json",
    "Prefer": "resolution=merge-duplicates,return=minimal",
})


def load(market):
    table = TABLE[market]
    rows = [json.loads(l) for l in open(f"iex_{market}.jsonl")]
    url = f"{PROJECT_URL}/rest/v1/{table}?on_conflict={ON_CONFLICT}"
    total = 0
    for i in range(0, len(rows), BATCH):
        chunk = rows[i:i + BATCH]
        for attempt in range(4):
            r = session.post(url, data=json.dumps(chunk), timeout=180)
            if r.status_code in (200, 201, 204):
                break
            if attempt == 3:
                print(f"  FAIL {table} batch {i}: {r.status_code} {r.text[:300]}")
                return
            time.sleep(2 * (attempt + 1))
        total += len(chunk)
        if (i // BATCH) % 20 == 0:
            print(f"  {table}: {total}/{len(rows)}", flush=True)
    print(f"{table}: {total} rows loaded")


for m in MARKETS:
    load(m)
