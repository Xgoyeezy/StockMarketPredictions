# Personal Trading Research Desk

FastAPI backend plus React frontend for self-directed market research, scanning, watchlists, trade planning, portfolio monitoring, alerts, notes, and own-account execution control.

This repo is now oriented toward private personal use, not a SaaS sale, white-label launch, or client advisory service. Treat model outputs, ranked setups, entries, targets, stops, and invalidation levels as research prompts that still require your review before any real-money decision.

## Project structure
- `backend/` - Python + FastAPI API, services, routers, models, and local storage
- `frontend/` - React + Vite UI

## Local development

### Backend
```bash
pip install -r backend/requirements.txt
python -m backend.app
```
API base path: `http://localhost:8000/api`

Managed detached backend runtime:

```bash
backend\.venv\Scripts\python.exe scripts/manage_api_runtime.py start --env-file .env
backend\.venv\Scripts\python.exe scripts/manage_api_runtime.py status --env-file .env
backend\.venv\Scripts\python.exe scripts/manage_api_runtime.py stop --env-file .env
```

Options paper-readiness diagnostic:

```bash
backend\.venv\Scripts\python.exe scripts/check_options_paper_readiness.py .env
```

### Canonical paper-options validation lane
Use `.env.staging` with local Docker Postgres for serious option-native paper validation. `.env` remains the lighter local/demo lane.

Bring up the staging lane in this order:

```bash
scripts\staging_ops.ps1 -Action db-up
scripts\staging_ops.ps1 -Action use-local-db
scripts\staging_ops.ps1 -Action env-check
scripts\staging_ops.ps1 -Action db-check
backend\.venv\Scripts\python.exe scripts/manage_api_runtime.py start --env-file .env.staging
backend\.venv\Scripts\python.exe scripts/manage_api_runtime.py status --env-file .env.staging
backend\.venv\Scripts\python.exe scripts/check_options_paper_readiness.py .env.staging
```

The staging runtime binds the API to `http://localhost:8001/api` and uses loopback-safe runtime probing on `http://127.0.0.1:8001/api`.

### Frontend
```bash
cd frontend
npm install
npm run dev
```
Frontend dev URL: `http://localhost:5173`

Canonical local startup flow:

```bash
make api-bg
make frontend
```

Production build plus shipped chart examples:

```bash
cd frontend
npm run build
```

Example build artifacts land under:

- `frontend/dist/examples/chart-demo/chart-demo.html`
- `frontend/dist/examples/chart-embed/chart-embed.html`

Copy `frontend/.env.example` to `frontend/.env` when you want to override the API base URL.

### Tick-by-tick streaming
The dashboard now supports a backend WebSocket proxy for true tick-by-tick stock trades and quotes.

1. Set `APCA_API_KEY_ID` and `APCA_API_SECRET_KEY` in `.env`
2. Keep `MARKET_DATA_PROVIDER=alpaca`
3. Choose the feed with `ALPACA_STOCK_FEED` such as `iex` or `sip`
4. Restart the backend after changing the environment

Without provider credentials, the UI falls back to snapshot-style polling and the realtime stream stays unavailable.
The backend auto-loads a project-level `.env`, so dropping the stream credentials in the repo root is enough for local development.

## Docker
```bash
cp .env.example .env
docker compose up --build
```

The Docker setup stores backend runtime files in the `backend-storage` volume.

## Verification
```bash
make test
make frontend-build
```

Personal-use operating notes are documented in:

- `PERSONAL_USE.md`
- `REAL_MONEY_EXECUTION_ROADMAP.md`

Backend verification buckets can be listed or run individually with:

```bash
make backend-groups
make backend-market
make backend-identity
make backend-ops
make backend-execution
```

## Operations
- Deployment readiness is surfaced in the release center from live files in this workspace.
- Backup posture is tracked in `runtime-logs/backup-status.json`.
- Operator runbooks live under `docs/runbooks/`.
- Personal-ops coverage now includes `slow_app`, `stale_feed`, and `backlog_recovery` runbooks in addition to deployment, rollback, backup, and incident response.
- Deployment probes are available at `/api/healthz` and `/api/readyz`.
- The local operator can export a diagnostics bundle from `/api/ops/diagnostics`.
- Update the backup manifest after each verified backup and restore drill.
- The dashboard runs on the proprietary market chart engine, and compatibility chart wrappers remain available for non-desk routes.
- The proprietary market chart engine lives in `frontend/src/chart-engine/` and `frontend/src/components/CustomMarketChart.jsx`.
- Legacy market chart wrappers now forward into the custom engine so non-desk routes can stay on the Milestone 6 chart baseline while the proprietary engine remains the source of truth.

## Notes
- Runtime data now lives under `backend/storage/`
- Static backend data now lives under `backend/data/`
- The Vite dev server proxies `/api` to the FastAPI backend
