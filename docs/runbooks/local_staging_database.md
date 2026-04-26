# Local Staging Database

Use this runbook when you want a real Postgres-backed staging-style backend boot on your local machine.

## What This Solves

- removes the placeholder `DATABASE_URL` blocker from `.env.staging`
- keeps staging validation on a real Postgres engine instead of SQLite
- avoids touching the frontend build or chart path

## One-Time Requirement

- Docker Desktop is installed and running

## Commands

1. Check local prerequisites:
   - `.\scripts\staging_ops.ps1 -Action preflight`
   - if Docker still looks unhealthy, run `.\scripts\staging_ops.ps1 -Action docker-diagnose`
2. Start the local Postgres container:
   - `.\scripts\staging_ops.ps1 -Action db-up`
3. Point `.env.staging` at that local database:
   - `.\scripts\staging_ops.ps1 -Action use-local-db`
4. Mark staging as local-only:
   - `.\scripts\staging_ops.ps1 -Action set-access-mode -AccessMode local`
5. Validate the env:
   - `.\scripts\staging_ops.ps1 -Action status`
   - `.\scripts\staging_ops.ps1 -Action env-check`
6. Validate the database connection:
   - `.\scripts\staging_ops.ps1 -Action db-check`
7. Print the backend boot command:
   - `.\scripts\staging_ops.ps1 -Action print-boot`
8. Start the backend with `.env.staging`
   - `.\scripts\staging_ops.ps1 -Action api`
9. Check the production floor:
   - `.\scripts\staging_ops.ps1 -Action floor-check`

## Local Database URL

The helper writes this value into `.env.staging`:

- `postgresql+psycopg://stocksignals:stocksignals_staging@localhost:54329/stocksignals_staging`

## Cleanup

- Stop the local staging database:
  - `.\scripts\staging_ops.ps1 -Action db-down`

## Notes

- This is for a local staging-style boot, not shared remote staging.
- Stripe can remain unconfigured while billing is still in non-live staging mode.
- Once a remote staging database exists, replace the local URL in `.env.staging` with that managed Postgres URL.
- You can set and inspect a managed URL with:
  - `.\scripts\staging_ops.ps1 -Action set-db-url -DatabaseUrl "postgresql+psycopg://..."`
  - `.\scripts\staging_ops.ps1 -Action show-db-url`
  - `.\scripts\staging_ops.ps1 -Action set-public-urls -FrontendUrl "https://staging.example.com" -ApiBaseUrl "https://api-staging.example.com/api"`
  - `.\scripts\staging_ops.ps1 -Action show-public-urls`
  - `.\scripts\staging_ops.ps1 -Action set-access-mode -AccessMode remote`
  - `.\scripts\staging_ops.ps1 -Action show-access-mode`
  - `.\scripts\staging_ops.ps1 -Action set-billing-mode -BillingMode disabled`
  - `.\scripts\staging_ops.ps1 -Action show-billing`
  - `.\scripts\staging_ops.ps1 -Action status`
