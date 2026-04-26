# Staging Smoke Checklist

Use this checklist immediately after the first `.env.staging` backend boot.

Purpose: confirm the app has moved from env-prep into a real non-demo runtime.

## Preconditions

1. `.env.staging` has a real `DATABASE_URL`
2. `.\scripts\staging_ops.ps1 -Action env-check` reports no blockers
3. the backend is started with `.env.staging` loaded into the process

Recommended boot command:

- `.\scripts\staging_ops.ps1 -Action api`

## Smoke Checks

### 1. Production Floor

Run:

- `.\scripts\staging_ops.ps1 -Action floor-check`

Pass target:

- environment is no longer blocked for development mode
- demo auth is no longer enabled
- local-demo auth is no longer the provider
- worker is running when the app process is up

### 2. Health Probes

Confirm:

- `/api/healthz` returns `200`
- `/api/readyz` is no longer blocked by environment defaults

### 3. Auth Posture

Confirm:

- auth config is no longer in `demo` mode
- `supports_login` reflects the configured path
- shell readiness banner no longer reports demo auth posture

### 4. Database Posture

Confirm:

- the app is pointed at the staging Postgres database
- the app can initialize and query successfully

### 5. Background Worker

Confirm:

- worker status reports `running` while the app is up
- worker is started by the normal app startup path

### 6. Backup Evidence

Confirm:

- backup and restore drill evidence has been captured outside the cleaned working tree when needed

## Exit Rule

The first staging smoke run passes only if:

- env validation passes
- production-floor validation has no environment/demo blockers
- health works
- auth is non-demo
- database is real
- worker is running

If any of these fail, record the blocker before proceeding to personal-use path validation.
