"""
Daily incremental updater for the Rates & Demand dashboard.

Runs as a Render Cron Job once a day. For a small rolling window of recent days
(CRON_DAYS, default 4) it re-fetches every source, parses it with the SAME
parser modules used for the historical backfill, and upserts the rows into
Supabase. Upserts are idempotent (merge-duplicates on the primary key), so
re-running is safe and late-published reports self-heal on the next run.

Writes use the SERVICE ROLE key (RLS blocks the anon key). After IEX loads it
refreshes the mv_iex_daily materialized view via an RPC.

Env:
  SUPABASE_URL                 (default: project URL below)
  SUPABASE_SERVICE_ROLE_KEY    (required — from Supabase > Settings > API)
  CRON_DAYS                    (optional, default 4)
"""
import importlib.util
import io
import os
import re
import sys
import time
import urllib.parse
from datetime import date, datetime, timedelta, timezone

import requests
import pdfplumber
import ssl
from requests.adapters import HTTPAdapter

try:
    from urllib3.util.ssl_ import create_urllib3_context
except Exception:  # older urllib3 layout
    from urllib3.util import create_urllib3_context  # type: ignore

requests.packages.urllib3.disable_warnings()


class _LegacyTLSAdapter(HTTPAdapter):
    """Some of the old govt TLS servers (erldc.in, srldc.in) require legacy
    SSL renegotiation, which OpenSSL 3 (Ubuntu 24.04) refuses by default with
    'UNSAFE_LEGACY_RENEGOTIATION_DISABLED'. Re-enable it (and skip cert checks,
    as several of these hosts have broken chains anyway)."""

    def init_poolmanager(self, *args, **kwargs):
        ctx = create_urllib3_context()
        ctx.options |= getattr(ssl, "OP_LEGACY_SERVER_CONNECT", 0x4)
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        kwargs["ssl_context"] = ctx
        return super().init_poolmanager(*args, **kwargs)


# Session used for ALL upstream data fetches (not for Supabase writes).
_HTTP = requests.Session()
_HTTP.mount("https://", _LegacyTLSAdapter())

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = os.path.dirname(HERE)

SUPABASE_URL = os.environ.get("SUPABASE_URL", "https://ltzulzadxqpwvfksmcfa.supabase.co")
SERVICE_KEY = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")
WINDOW = int(os.environ.get("CRON_DAYS", "4"))
IST = timezone(timedelta(hours=5, minutes=30))


def log(msg):
    print(f"[{datetime.now(IST).isoformat(timespec='seconds')}] {msg}", flush=True)


def load_parser(name, rel):
    spec = importlib.util.spec_from_file_location(name, os.path.join(SCRIPTS, rel))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


P = {
    "nldc": load_parser("p_nldc", "nldc/parser.py"),
    "nrldc": load_parser("p_nrldc", "nrldc/parser.py"),
    "wrldc": load_parser("p_wrldc", "wrldc/parser.py"),
    "erldc": load_parser("p_erldc", "erldc/parser.py"),
    "srldc": load_parser("p_srldc", "srldc/parser.py"),
    "nerldc": load_parser("p_nerldc", "nerldc/parser.py"),
    "iex": load_parser("p_iex", "iex/parser.py"),
}

# --- Supabase upsert ---------------------------------------------------------
_sess = requests.Session()
_sess.headers.update({
    "apikey": SERVICE_KEY,
    "Authorization": f"Bearer {SERVICE_KEY}",
    "Content-Type": "application/json",
    "Prefer": "resolution=merge-duplicates,return=minimal",
})
_counts = {}


def upsert(table, rows, on_conflict):
    if not rows:
        return
    url = f"{SUPABASE_URL}/rest/v1/{table}?on_conflict={on_conflict}"
    for attempt in range(4):
        r = _sess.post(url, data=_json(rows), timeout=120)
        if r.status_code in (200, 201, 204):
            _counts[table] = _counts.get(table, 0) + len(rows)
            return
        if attempt == 3:
            log(f"  UPSERT FAIL {table}: {r.status_code} {r.text[:200]}")
            return
        time.sleep(2 * (attempt + 1))


def _json(obj):
    import json
    return json.dumps(obj, default=str)


def http(url, verify=False, headers=None, method="GET", json_body=None):
    # verify kept for call-site compatibility; the _HTTP adapter handles TLS.
    for attempt in range(3):
        try:
            if method == "POST":
                r = _HTTP.post(url, json=json_body, headers=headers, timeout=60)
            else:
                r = _HTTP.get(url, headers=headers, timeout=60)
            return r
        except Exception as e:
            if attempt == 2:
                raise
            time.sleep(1.5 * (attempt + 1))


def window_days():
    today = datetime.now(IST).date()
    return [today - timedelta(days=i) for i in range(0, WINDOW + 1)]


# --- RLDC generic (pdf, parse_report -> regional/state_demand/state_generation)
RLDC_OC = {
    "regional_availability": "report_date",
    "state_demand": "report_date,state_canonical",
    "state_generation": "report_date,state_canonical,seq",
}


def push_rldc(src, rd, data):
    if data is None:
        return False
    if data.get("regional"):
        upsert(f"{src}_regional_availability", [{**data["regional"], "report_date": rd}], RLDC_OC["regional_availability"])
    upsert(f"{src}_state_demand", [{**r, "report_date": rd} for r in data.get("state_demand", [])], RLDC_OC["state_demand"])
    upsert(f"{src}_state_generation", [{**r, "report_date": rd} for r in data.get("state_generation", [])], RLDC_OC["state_generation"])
    return True


def parse_pdf(content):
    with pdfplumber.open(io.BytesIO(content)) as pdf:
        return pdf  # not used directly


def rldc_url_source(src, url_fn, days):
    ok = 0
    for d in days:
        try:
            r = http(url_fn(d))
            if r.status_code != 200 or len(r.content) < 3000:
                continue
            with pdfplumber.open(io.BytesIO(r.content)) as pdf:
                data = P[src].parse_report(pdf)
            if push_rldc(src, d.isoformat(), data):
                ok += 1
        except Exception as e:
            log(f"  {src} {d}: {str(e)[:120]}")
    log(f"{src}: {ok} days upserted")


def do_wrldc(days):
    rldc_url_source("wrldc", lambda d:
        f"https://reporting.wrldc.in:8081/PSP/{d.year}/{d.strftime('%B')}/WRLDC_PSP_Report_{d.strftime('%d-%m-%Y')}.pdf", days)


def do_srldc(days):
    rldc_url_source("srldc", lambda d:
        f"https://www.srldc.in/var/ftp/reports/psp/{d.year}/{d.strftime('%b%y')}/{d.strftime('%d-%m-%Y')}-psp.pdf", days)


def do_nerldc(days):
    rldc_url_source("nerldc", lambda d:
        f"https://www.nerldc.in/wp-content/uploads/NER-PSP-REPORT-DATED-{d.strftime('%d-%m-%Y')}.pdf", days)


_NR_MON = {m: i for i, m in enumerate(
    ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"], 1)}


def nrldc_body_date(pdf):
    t = (pdf.pages[0].extract_text() or "").replace("\n", " ")
    m = re.search(r"For\s*(\d{2})-([A-Za-z]{3})-(\d{4})", t)
    if not m or m.group(2) not in _NR_MON:
        return None
    dd, mon, yyyy = m.groups()
    return date(int(yyyy), _NR_MON[mon], int(dd)).isoformat()


def do_nrldc(days):
    win = {d.isoformat() for d in days}
    r = http("https://nrldc.in/get-documents-list/111?start_date=&end_date=&draw=1&start=0&length=15",
             verify=True, headers={"User-Agent": "Mozilla/5.0", "X-Requested-With": "XMLHttpRequest"})
    ok = 0
    for row in (r.json().get("data") or []):
        m = re.search(r"any=([^'\"]+)", row.get("download", ""))
        if not m:
            continue
        try:
            c = http("https://nrldc.in/download-file?any=" + m.group(1), verify=False).content
            if not c or len(c) < 1000:
                continue
            with pdfplumber.open(io.BytesIO(c)) as pdf:
                rd = nrldc_body_date(pdf)
                if rd is None or rd not in win:
                    continue
                data = P["nrldc"].parse_report(pdf)
            if push_rldc("nrldc", rd, data):
                ok += 1
        except Exception as e:
            log(f"  nrldc: {str(e)[:120]}")
    log(f"nrldc: {ok} days upserted")


def do_erldc(days):
    win = {d.isoformat() for d in days}
    r = http("https://erldc.in/api//fetchAllStandardData", method="POST", verify=True,
             json_body={"targetTableClass": "DailyPSPReport"})
    prods = (r.json().get("data") or {}).get("products") or []
    ok = 0
    for p in prods:
        epoch = p.get("fileDate")
        if not epoch:
            continue
        rd = datetime.fromtimestamp(epoch / 1000, IST).date().isoformat()
        if rd not in win:
            continue
        try:
            c = http("https://erldc.in/api//downloadFile/DailyPSPReport/" + p["id"], verify=True).content
            if not c or len(c) < 1000:
                continue
            with pdfplumber.open(io.BytesIO(c)) as pdf:
                data = P["erldc"].parse_report(pdf)
            if push_rldc("erldc", rd, data):
                ok += 1
        except Exception as e:
            log(f"  erldc {rd}: {str(e)[:120]}")
    log(f"erldc: {ok} days upserted")


NLDC_OC = {
    "regional": ("nldc_regional_psp", "report_date,region"),
    "state": ("nldc_state_psp", "report_date,state_canonical"),
    "gen_outage": ("nldc_generation_outage", "report_date,region"),
    "sourcewise_gen": ("nldc_sourcewise_generation", "report_date,region"),
    "solar_nonsolar": ("nldc_solar_nonsolar_peak", "report_date,hour_type"),
}


def _fy(d):
    return f"{d.year}-{str(d.year + 1)[2:]}" if d.month >= 4 else f"{d.year - 1}-{str(d.year)[2:]}"


def do_nldc(days):
    win = {d.isoformat() for d in days}
    fymonths = {(_fy(d), f"{d.month:02d}") for d in days}
    headers = {"Content-Type": "application/json", "Referer": "https://grid-india.in/"}
    ok = 0
    for fy, month in fymonths:
        try:
            r = http("https://webapi.grid-india.in/api/v1/file", method="POST", verify=False,
                     headers=headers, json_body={"_source": "GRDW", "_type": "DAILY_PSP_REPORT", "_fileDate": fy, "_month": month})
            entries = r.json().get("retData") or []
        except Exception as e:
            log(f"  nldc list {fy}/{month}: {str(e)[:100]}")
            continue
        by_date = {}
        for e in entries:
            d = e.get("Field2")
            if d not in win:
                continue
            is_excel = "excel" in e.get("MimeType", "") or e.get("FilePath", "").endswith(".xls")
            cur = by_date.get(d)
            if cur is None or (is_excel and not ("excel" in cur.get("MimeType", "") or cur.get("FilePath", "").endswith(".xls"))):
                by_date[d] = e
        for d, e in by_date.items():
            try:
                content = http("https://webcdn.grid-india.in/" + e["FilePath"], verify=False).content
                if not content:
                    continue
                if "excel" in e.get("MimeType", "") or e["FilePath"].endswith(".xls"):
                    tables = P["nldc"].parse_excel(file_contents=content)
                    fmt = "excel"
                else:
                    with pdfplumber.open(io.BytesIO(content)) as pdf:
                        text = "\n".join(pg.extract_text() or "" for pg in pdf.pages)
                    tables = P["nldc"].parse_pdf_text(text)
                    fmt = "pdf"
                for key, (table, oc) in NLDC_OC.items():
                    rows = [{**row, "report_date": d, "source_format": fmt} for row in tables.get(key, [])]
                    upsert(table, rows, oc)
                ok += 1
            except Exception as ex:
                log(f"  nldc {d}: {str(ex)[:120]}")
    log(f"nldc: {ok} days upserted")


def do_iex(days):
    for market, is_rtm in (("dam", False), ("rtm", True)):
        ok = 0
        for d in days:
            ds = d.strftime("%d-%m-%Y")
            slug = "real-time-market" if is_rtm else "day-ahead-market"
            url = (f"https://www.iexindia.com/market-data/{slug}/market-snapshot"
                   f"?interval=ONE_FOURTH_HOUR&dp=SELECT_RANGE&fromDate={ds}&toDate={ds}")
            try:
                html = http(url, verify=False, headers={"User-Agent": "Mozilla/5.0"}).text
                rows = P["iex"].parse_day(html, is_rtm)
                if not rows:
                    continue
                upsert(f"iex_{market}", [{**r, "report_date": d.isoformat()} for r in rows], "report_date,block")
                ok += 1
            except Exception as e:
                log(f"  iex {market} {d}: {str(e)[:120]}")
        log(f"iex_{market}: {ok} days upserted")


def refresh_matview():
    try:
        r = _sess.post(f"{SUPABASE_URL}/rest/v1/rpc/refresh_iex_daily", data="{}", timeout=120)
        log(f"refresh mv_iex_daily: HTTP {r.status_code}")
    except Exception as e:
        log(f"refresh mv_iex_daily failed: {str(e)[:120]}")


def main():
    if not SERVICE_KEY:
        log("ERROR: SUPABASE_SERVICE_ROLE_KEY not set")
        sys.exit(1)
    days = window_days()
    log(f"daily update — window {days[-1]} .. {days[0]} ({len(days)} days)")
    for name, fn in [
        ("nldc", do_nldc), ("nrldc", do_nrldc), ("wrldc", do_wrldc),
        ("erldc", do_erldc), ("srldc", do_srldc), ("nerldc", do_nerldc), ("iex", do_iex),
    ]:
        try:
            fn(days)
        except Exception as e:
            log(f"{name}: SOURCE FAILED {str(e)[:160]}")
    refresh_matview()
    log(f"upserted rows by table: {_counts}")
    log("done")


if __name__ == "__main__":
    main()
