# Deployment Runbook

## Goal
Promote the current stack safely with a repeatable local or staging deployment path.

## Preconditions
- `.env` is present and reviewed.
- `docker compose config` succeeds.
- `make test` and `make frontend-build` pass.
- `runtime-logs/backup-status.json` has been updated for the latest backup.
- Release validation has a known rollback target.
- Release evidence excludes secrets, broker records, account identifiers, raw logs, raw local paths, database files, credentials, and environment values.
- Safety invariants confirm the release does not enable live trading, change broker routes, modify order submission logic, bypass risk gates, clear kill switches, grant AI order authority, merge simulation evidence into market-observed evidence, or mutate ranking weights.

## Steps
1. Pull the latest code and review the release center for open blockers.
2. Verify API health locally with `python -m backend.app` or in Docker with `docker compose up --build`.
3. Run `make test`.
4. Run `make frontend-build`.
5. Deploy with `docker compose up --build -d`.
6. Verify:
   - `GET /api/health`
   - frontend loads at `http://localhost:5173`
   - recent jobs are draining
   - no new dead-letter jobs appear

## Post-deploy checks
- Confirm release center latency and job backlog look normal.
- Record the deployment timestamp and any anomalies in the incident log if needed.
- Record release validation metadata with the release identifier, changed proof surfaces, safety invariant result, test/build/probe summary, reviewer or automation check, decision, rollback target, and sanitization result.
- Keep release validation metadata as review evidence only. It must not change broker routes, order behavior, risk gates, kill switches, AI authority, ranking weights, or live-trading state.
