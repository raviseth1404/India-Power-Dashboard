"""
Backfill IEX market-snapshot data (day-ahead or real-time) day by day.

Usage:  python backfill.py {dam|rtm} FROM_ISO TO_ISO

One SSR HTML request per day (the range/pagination is client-side only, so the
server always renders just fromDate's 96 blocks -> fetch each day with
fromDate==toDate). Resumable: appends to iex_{market}.jsonl and, on restart,
skips any report_date already written, so it survives the ~20-min background cap.
Process pool for network parallelism (fetch is IO-bound but the HTML regex
parse is light, so threads would be fine too; pool keeps it uniform with the
other sources).
"""
import io
import json
import os
import ssl
import sys
import urllib.request
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import date, timedelta, datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from parser import parse_day

MARKET = {
    "dam": ("day-ahead-market", False),
    "rtm": ("real-time-market", True),
}
URL = ("https://www.iexindia.com/market-data/{slug}/market-snapshot"
       "?interval=ONE_FOURTH_HOUR&dp=SELECT_RANGE&fromDate={d}&toDate={d}")

_CTX = ssl.create_default_context()
_CTX.check_hostname = False
_CTX.verify_mode = ssl.CERT_NONE


def worker(args):
    iso, slug, is_rtm = args
    d = date.fromisoformat(iso)
    ds = d.strftime("%d-%m-%Y")
    url = URL.format(slug=slug, d=ds)
    for attempt in range(3):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            html = urllib.request.urlopen(req, context=_CTX, timeout=90).read().decode("utf-8", "ignore")
            rows = parse_day(html, is_rtm)
            if not rows:
                return ("empty", iso, None)
            return ("ok", iso, rows)
        except Exception as e:
            if attempt == 2:
                return ("fail", iso, str(e)[:120])
    return ("fail", iso, "retries")


def log(msg):
    line = f"[{datetime.now().isoformat(timespec='seconds')}] {msg}"
    print(line, flush=True)
    with open(f"backfill_{sys.argv[1]}.log", "a") as f:
        f.write(line + "\n")


def main():
    market = sys.argv[1]
    slug, is_rtm = MARKET[market]
    start = date.fromisoformat(sys.argv[2])
    end = date.fromisoformat(sys.argv[3])
    out_path = f"iex_{market}.jsonl"

    done = set()
    if os.path.exists(out_path):
        with open(out_path) as f:
            for line in f:
                try:
                    done.add(json.loads(line)["report_date"])
                except Exception:
                    pass
    log(f"{market}: resume, {len(done)} days already done")

    days = []
    d = start
    while d <= end:
        iso = d.isoformat()
        if iso not in done:
            days.append((iso, slug, is_rtm))
        d += timedelta(days=1)
    log(f"{market}: {len(days)} days to fetch")

    ok = empty = fail = 0
    buf = []
    fout = open(out_path, "a")

    def flush():
        for r in buf:
            fout.write(json.dumps(r) + "\n")
        fout.flush()
        buf.clear()

    with ProcessPoolExecutor(max_workers=8) as pool:
        futures = [pool.submit(worker, a) for a in days]
        for fut in as_completed(futures):
            status, iso, payload = fut.result()
            if status == "empty":
                empty += 1
                continue
            if status == "fail":
                fail += 1
                log(f"  FAIL {iso}: {payload}")
                continue
            ok += 1
            for rec in payload:
                buf.append({**rec, "report_date": iso})
            if len(buf) >= 5000:
                flush()
            if ok % 300 == 0:
                flush()
                log(f"  progress: {ok} ok, {empty} empty, {fail} fail")
    flush()
    fout.close()
    log(f"DONE {market}. ok={ok} empty={empty} fail={fail}")


if __name__ == "__main__":
    main()
