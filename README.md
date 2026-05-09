# Quant Evidence OS / StockMarketPredictions

Quant Evidence OS is a Python/FastAPI plus React/Vite trading research and evidence-control platform for scanning markets, reviewing strategy candidates, validating forecasts, comparing evidence, and auditing why trades did or did not happen.

This is not a guaranteed-profit system, investment adviser, HFT platform, or autonomous live-money trading bot. Treat model outputs, ranked setups, entries, targets, stops, and invalidation levels as research prompts that still require human review before any real-money decision.

## Public visibility

This repository is public for portfolio review, technical feedback, and discussion. You can:

- Open an issue with a bug report, question, or feature suggestion.
- Start a discussion for architecture, research workflow, or product-positioning feedback.
- Submit a pull request for review.

Direct changes to `main` are restricted. Suggestions and pull requests are reviewed before anything is accepted.

## Safety boundaries

- Paper-first research and validation remain the active posture.
- No autonomous live-money order path is enabled.
- AI has no order authority.
- Risk gates remain authoritative.
- Broker routes are not loosened by analytics.
- Forecast and reward analytics are research-only.
- Simulation evidence stays separate from market-observed evidence.

## License and reuse

No open-source license is currently granted. The code is source-available for viewing and feedback only unless the repository owner gives written permission for other use. See `LICENSE`.

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

The Docker setup runs Postgres, applies Alembic migrations before API startup, and stores backend runtime files in the `backend-storage` volume. Alembic is the source of truth for productized schema changes; local startup still keeps runtime `create_all()` compatibility for developer ergonomics.

First-run bootstraps a default tenant + admin (only when the database is empty). Credentials are written to:

- `runtime-logs/first-run-credentials.json`

By default the Docker runtime enables local-session auth (`AUTH_ENABLED=true`, `ALLOW_DEMO_AUTH=false`) and disables self-serve signup (`LOCAL_AUTH_ALLOW_SIGNUP=false`). If `LOCAL_AUTH_LOGIN_SECRET` is not provided, the container generates one and persists it in the credentials file above.

Useful migration commands:

```bash
python -m alembic -c alembic.ini upgrade head
python -m alembic -c alembic.ini downgrade base
```

Live trading and managed-advisory flags default off:

```bash
FEATURE_LIVE_TRADING=false
FEATURE_MANAGED_ADVISORY=false
READINESS_MIN_LIVE_SCORE=85
ALPACA_LIVE_TRADING_ENABLED=false
```

## Verification
```bash
make test
make frontend-build
```

Personal-use operating notes are documented in:

- `docs/PERSONAL_USE.md`
- `docs/broker_trading_desk_architecture.md`
- `docs/compliance_checklist.md`
- `docs/readiness_scoring.md`
- `docs/live_trading_flow.md`
- `docs/trading_safety_hardening.md`
- `docs/hidden_ops_route_inventory.md`

## Premium live trading control-plane controls

The productized control plane is a paper-validated live automation desk layered over connected broker accounts. Connected brokers handle custody, account statements, execution, and regulatory brokerage functions; this app handles strategy lifecycle, readiness scoring, risk gates, audit replay, execution evidence, and user-authorized automation.

The control plane exposes:

- `/api/strategies/*`
- `/api/automation/*`
- `/api/readiness/*`
- `/api/risk/*`
- `/api/audit/*`
- `/api/execution-analytics/*`
- `/api/live/*`
- `/api/live/authorizations/*`
- `/api/live/orders/*`

Frontend routes:

- `/strategies`
- `/strategies/:strategyId`
- `/strategies/:strategyId/live`
- `/risk`
- `/audit`
- `/execution-quality`
- `/live`
- `/live/approvals`

The live-control invariant is server-side: no signal path submits directly to a live broker. A live flow must create durable `TradeDecision`, `LiveOrderIntent`, `LiveRiskCheck`, approval, receipt, risk event, and audit/domain evidence. The app does not provide custody, clearing, statements, SIPC membership, or broker-dealer operation; those remain connected-broker responsibilities.

Backend verification buckets can be listed or run individually with:

```bash
make backend-groups
make backend-market
make backend-identity
make backend-ops
make backend-execution
```

Trading-safety readiness can be checked without exposing secrets:

```bash
python scripts/trading_safety_tools.py market-ready --env-file .env --tenant-slug systematic-equities
python scripts/trading_safety_tools.py route-table
python scripts/trading_safety_tools.py weak-strong-sweep
```

The market-ready report combines env presence checks, latest safety state, daily ledger summary, route-table health, HFT watchdog status, artifact indexing, and weak/strong scan findings. It writes a validation artifact under `runtime-exports/`.

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
