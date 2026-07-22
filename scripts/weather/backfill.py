"""
Backfill daily weather for ~19 Indian load-centre cities from the Open-Meteo
historical archive (ERA5 reanalysis, free, no key) into Supabase weather_daily.

One API call per city covers the whole 2012 -> present range. The archive lags
realtime by ~5 days; the daily cron will later top up recent days from the
forecast API's past_days. Usage:

  SUPABASE_SERVICE_ROLE_KEY=... python backfill.py [start] [end]
(or put the key in scripts/.env — see run helper)
"""
import json
import os
import sys
import time
from datetime import date, timedelta

import requests

PROJECT_URL = "https://ltzulzadxqpwvfksmcfa.supabase.co"
API_KEY = os.environ["SUPABASE_SERVICE_ROLE_KEY"]
START = sys.argv[1] if len(sys.argv) > 1 else "2012-01-01"
END = sys.argv[2] if len(sys.argv) > 2 else (date.today() - timedelta(days=2)).isoformat()

# (city, state, region, lat, lon) — major load centres across the 5 regions.
CITIES = [
    ("Delhi", "Delhi", "NR", 28.61, 77.21),
    ("Lucknow", "Uttar Pradesh", "NR", 26.85, 80.95),
    ("Jaipur", "Rajasthan", "NR", 26.91, 75.79),
    ("Ludhiana", "Punjab", "NR", 30.90, 75.85),
    ("Hisar", "Haryana", "NR", 29.15, 75.72),
    ("Mumbai", "Maharashtra", "WR", 19.08, 72.88),
    ("Nagpur", "Maharashtra", "WR", 21.15, 79.09),
    ("Ahmedabad", "Gujarat", "WR", 23.03, 72.58),
    ("Bhopal", "Madhya Pradesh", "WR", 23.26, 77.41),
    ("Raipur", "Chhattisgarh", "WR", 21.25, 81.63),
    ("Chennai", "Tamil Nadu", "SR", 13.08, 80.27),
    ("Bengaluru", "Karnataka", "SR", 12.97, 77.59),
    ("Hyderabad", "Telangana", "SR", 17.39, 78.49),
    ("Vijayawada", "Andhra Pradesh", "SR", 16.51, 80.65),
    ("Kochi", "Kerala", "SR", 9.93, 76.27),
    ("Kolkata", "West Bengal", "ER", 22.57, 88.36),
    ("Patna", "Bihar", "ER", 25.59, 85.14),
    ("Bhubaneswar", "Odisha", "ER", 20.30, 85.82),
    ("Guwahati", "Assam", "NER", 26.14, 91.74),
]

DAILY_VARS = ("temperature_2m_max,temperature_2m_min,temperature_2m_mean,"
              "precipitation_sum,shortwave_radiation_sum,wind_speed_10m_max")

sess = requests.Session()
sess.headers.update({
    "apikey": API_KEY, "Authorization": f"Bearer {API_KEY}",
    "Content-Type": "application/json",
    "Prefer": "resolution=merge-duplicates,return=minimal",
})


def fetch_city(city, lat, lon):
    url = ("https://archive-api.open-meteo.com/v1/archive"
           f"?latitude={lat}&longitude={lon}&start_date={START}&end_date={END}"
           f"&daily={DAILY_VARS}&timezone=Asia%2FKolkata")
    for attempt in range(4):
        r = requests.get(url, timeout=120)
        if r.status_code == 200:
            return r.json()["daily"]
        time.sleep(5 * (attempt + 1))
    raise RuntimeError(f"{city}: HTTP {r.status_code} {r.text[:200]}")


def upsert(rows):
    url = f"{PROJECT_URL}/rest/v1/weather_daily?on_conflict=report_date,city"
    for i in range(0, len(rows), 4000):
        chunk = rows[i:i + 4000]
        for attempt in range(3):
            r = sess.post(url, data=json.dumps(chunk), timeout=120)
            if r.status_code in (200, 201, 204):
                break
            if attempt == 2:
                raise RuntimeError(f"upsert fail: {r.status_code} {r.text[:200]}")
            time.sleep(3)


def main():
    total = 0
    for city, state, region, lat, lon in CITIES:
        d = fetch_city(city, lat, lon)
        rows = []
        for i, day in enumerate(d["time"]):
            rows.append({
                "report_date": day, "city": city, "state": state, "region": region,
                "tmax_c": d["temperature_2m_max"][i],
                "tmin_c": d["temperature_2m_min"][i],
                "tmean_c": d["temperature_2m_mean"][i],
                "rain_mm": d["precipitation_sum"][i],
                "solar_rad_mj_m2": d["shortwave_radiation_sum"][i],
                "wind_max_kmh": d["wind_speed_10m_max"][i],
            })
        upsert(rows)
        total += len(rows)
        print(f"{city}: {len(rows)} days loaded (total {total})", flush=True)
        time.sleep(1)  # be polite to the free API
    print(f"DONE: {total} rows")


if __name__ == "__main__":
    main()
