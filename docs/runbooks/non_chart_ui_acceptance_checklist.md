# Non-Chart UI Acceptance Checklist

Last updated: 2026-04-17

Purpose: validate the user-facing first-value path in staging without touching the chart or workstation rewrite.

Base URL under test:

- Frontend: `http://localhost:5173`
- API: `http://localhost:8001/api`

Current scope:

- Included:
  - login and tenant handoff
  - readiness and error messaging
  - watchlist workflow
  - saved workspaces workflow
  - tenant onboarding and settings workflow
- Excluded:
  - dashboard chart rendering
  - chart overlays, drawing tools, or chart layout persistence
  - any route or component that depends on `CustomMarketChart`

## Safe Routes

These routes are the preferred first-value validation path because they are not the active chart rewrite surface.

- `/watchlist`
- `/workspaces`
- `/settings`
- `/release`

Use `/` only to confirm shell load, auth redirect behavior, and readiness messaging. Do not treat dashboard chart behavior as an acceptance blocker in this tranche.

## Preflight

Before running the UI pass:

1. Confirm staging API is live with:
   - `.\scripts\staging_ops.ps1 -Action live-check`
2. Confirm backend-side acceptance still passes with:
   - `.\scripts\staging_ops.ps1 -Action acceptance-smoke`
3. Confirm safe UI routes are still isolated from chart code:
   - `.\scripts\staging_ops.ps1 -Action route-audit`
4. Confirm frontend is pointing at staging API:
   - `.\scripts\staging_ops.ps1 -Action show-frontend-target`
   - expected API base URL: `http://localhost:8001/api`
5. If needed, switch the frontend local target to staging:
   - `.\scripts\staging_ops.ps1 -Action set-frontend-target -ApiBaseUrl http://localhost:8001/api`
6. Confirm frontend UI preflight:
   - `.\scripts\staging_ops.ps1 -Action frontend-preflight`
   - if the dev server was already running before the env change, restart it before the manual UI pass
7. Optional all-in-one readiness gate:
   - `.\scripts\staging_ops.ps1 -Action ui-readiness`
8. Seed a known-good tenant for the manual UI pass:
   - `.\scripts\staging_ops.ps1 -Action ui-seed`
   - latest session file: `runtime-exports/non_chart_ui_latest_session.md`
9. Open the latest non-chart session in the browser:
   - `.\scripts\staging_ops.ps1 -Action ui-open`

## Acceptance Flow

### Step 1: Login And Shell Truthfulness

- Open the frontend.
- Sign in with a staging tenant.
- Confirm the app shell loads without redirect loops.
- Confirm the shell shows the non-production readiness banner.
- Confirm there is no false "healthy demo" behavior when auth or bootstrap fails.

Pass criteria:

- Login succeeds.
- Tenant context is present in the URL after login.
- Shell messaging accurately reflects staging state.

### Step 2: Watchlist First Value

- Navigate to `/watchlist`.
- Confirm the page loads rows without crashing.
- Confirm live prices and signal rows appear.
- Change tickers and refresh.
- Save a watchlist workspace.

Pass criteria:

- Watchlist renders.
- Refresh works.
- Save workspace succeeds.

### Step 3: Workspace Reuse

- Navigate to `/workspaces`.
- Confirm the seeded `Personal Launchpad` workspace and any saved watchlist workspace appear.
- Apply a workspace.
- Pin or unpin a workspace.
- Duplicate a workspace.

Pass criteria:

- Workspace list loads.
- Apply succeeds and routes to the intended page.
- Pin and duplicate succeed.

### Step 4: Tenant Settings And Onboarding

- Navigate to `/settings`.
- Confirm organizations load.
- Confirm billing summary and entitlements render.
- Confirm onboarding snapshot renders.
- Confirm support snapshot renders.
- Seed a starter workspace only if none exists yet.

Pass criteria:

- Tenant settings sections render without API fallback confusion.
- Billing, entitlements, onboarding, and support data all load.
- Onboarding progress reflects workspace seeding truthfully.

### Step 5: Release Surface Sanity Check

- Navigate to `/release`.
- Confirm the page loads without chart-related breakage.
- Confirm release and tenant metadata render.

Pass criteria:

- Route loads and shows data or empty-state truthfully.

## Record Results

After each run, append the outcome to:

- `runtime-exports/non_chart_ui_runs.md`
- or use:
  - `.\scripts\staging_ops.ps1 -Action ui-record -Result pass-with-chart-excluded -Notes "watchlist passed|workspaces passed|settings passed|release passed"`

Suggested result labels:

- `pass`
- `fail`
- `pass-with-chart-excluded`

## Known Non-Blockers For This Tranche

These should not fail the run unless they break the non-chart routes above:

- dashboard chart rendering issues
- chart import/build problems tied to the current rewrite
- workstation overlay behavior
- drawing tools or chart shortcuts

## Exit Condition

This tranche is complete when:

- backend-side acceptance smoke passes
- `/watchlist`, `/workspaces`, `/settings`, and `/release` all load cleanly in staging
- the user can get to first value without needing the chart route
