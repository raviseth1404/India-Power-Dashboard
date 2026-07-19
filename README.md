# India Power Markets & Supply Dashboard

A public dashboard of India's daily electricity **supply position** (NLDC + the
five regional load despatch centres) and **IEX market prices** (Day-Ahead and
Real-Time Market), with both a latest-day snapshot and historical trends.

- **Data**: ~1.9M rows in Supabase across 7 sources (NLDC, NRLDC, WRLDC, ERLDC,
  SRLDC, NERLDC, IEX DAM/RTM). Read-only Row-Level Security on every table.
- **Frontend**: Next.js (App Router) + Chart.js — see [`frontend/`](frontend/).
- **Ingestion**: Python parsers per source in [`scripts/`](scripts/); a daily
  Render Cron Job ([`scripts/cron/`](scripts/cron/)) keeps everything current.

## Repository layout
```
frontend/          Next.js dashboard (deploy to Vercel/Netlify)
scripts/
  nldc/ nrldc/ …   historical backfill + parser per source
  iex/             IEX DAM/RTM fetch + parser
  cron/            daily_update.py — the Render Cron Job updater
render.yaml        Render Blueprint for the daily cron
```

## Data coverage
| Source | Range | Granularity |
|--------|-------|-------------|
| NLDC (national + regions) | 2013-04 → present | daily PSP |
| WRLDC / SRLDC / NERLDC | 2018–2019 → present | daily PSP |
| NRLDC / ERLDC | 2024 → present | daily PSP |
| IEX DAM | 2012-04 → present | 15-min blocks |
| IEX RTM | 2020-06 → present | 15-min blocks |

## Running

**Frontend**
```bash
cd frontend
cp .env.example .env.local   # fill in Supabase URL + anon key
npm install && npm run dev
```

**Daily updater** (see [`scripts/cron/README.md`](scripts/cron/README.md))
```bash
pip install -r scripts/cron/requirements.txt
SUPABASE_SERVICE_ROLE_KEY=xxxxx python scripts/cron/daily_update.py
```

## Notes
- The IEX cleared-volume convention: RTM energy per 15-min block = `MCV ÷ 4`
  (MWh); DAM `MCV` is shown as reported. Implemented once in
  `frontend/lib/units.ts`.
- Writes (cron) use the Supabase **service_role** key (RLS blocks anon writes);
  the frontend uses only the public anon key.
- Large regenerable artifacts (Excel exports, intermediate `.jsonl`) are
  git-ignored — re-fetch/rebuild from the scripts.
