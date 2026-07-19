# Daily updater (Render Cron Job)

`daily_update.py` re-fetches a rolling window of recent days for every source,
parses each with the same parser modules used for the historical backfill, and
upserts the rows into Supabase. Upserts are idempotent (merge-duplicates on the
primary key), so a run is safe to repeat and late-published reports self-heal on
the next run. After IEX loads it refreshes the `mv_iex_daily` materialized view.

## Sources & how each recent day is fetched
| Source | Fetch | report_date from |
|--------|-------|------------------|
| NLDC | grid-india API list → CDN file (prefers Excel) | API `Field2` |
| NRLDC | `get-documents-list/111` → `download-file` | PDF body ("For DD-Mon-YYYY") |
| WRLDC | date-based PDF URL (full month name) | URL date |
| ERLDC | `fetchAllStandardData` list → `downloadFile/{id}` | `fileDate` epoch |
| SRLDC | date-based PDF URL | URL date |
| NERLDC | date-based PDF URL | URL date |
| IEX DAM/RTM | per-day SSR HTML | URL date |

## Environment
- `SUPABASE_URL` — project URL (default baked in).
- `SUPABASE_SERVICE_ROLE_KEY` — **required**. RLS blocks the anon key from
  writing; the cron must use the service_role key (Supabase > Settings > API).
  Keep it server-side only — never in the frontend.
- `CRON_DAYS` — rolling window size (default 4). A larger window heals longer
  gaps at the cost of more requests.

## Run locally
```bash
pip install -r scripts/cron/requirements.txt
SUPABASE_SERVICE_ROLE_KEY=xxxxx CRON_DAYS=4 python scripts/cron/daily_update.py
```

## Deploy on Render
Use `render.yaml` at the repo root (Blueprint), or create a Cron Job manually:
- Build: `pip install -r scripts/cron/requirements.txt`
- Command: `python scripts/cron/daily_update.py`
- Schedule: `0 3 * * *` (08:30 IST)
- Env: set `SUPABASE_SERVICE_ROLE_KEY`.
