# Staging Launch Checklist

Use this checklist before calling a non-demo environment `staging-ready`.

## 1. Environment File

1. Create a real staging env file from [.env.staging.example](/D:/marcc/PycharmProjects/StockMarketPredictions/.env.staging.example).
2. Fill all non-placeholder values for:
   - `DATABASE_URL`
   - `AUTH_SESSION_SECRET`
   - `AUTH_STATE_SECRET`
   - `API_TOKEN_SALT`
   - `PUBLIC_API_BASE_URL`
   - `APCA_API_KEY_ID`
   - `APCA_API_SECRET_KEY`
3. Decide whether Stripe is:
   - intentionally not configured yet
   - fully configured for staging billing validation
   - set that posture explicitly with:
     - `.\scripts\staging_ops.ps1 -Action set-billing-mode -BillingMode disabled`
     - or `.\scripts\staging_ops.ps1 -Action set-billing-mode -BillingMode test_stripe -StripePublishableKey "pk_test_..." -StripeSecretKey "sk_test_..." -StripeWebhookSecret "whsec_..."`

When `STAGING_BILLING_MODE=disabled`, missing Stripe keys are treated as intentional staging posture instead of a validator warning.

## 2. Validate Env Before Boot

Run:

- `.\scripts\staging_ops.ps1 -Action status`
- `.\scripts\staging_ops.ps1 -Action runtime-gate`
- `.\scripts\staging_ops.ps1 -Action env-check`

Pass condition:

- no blockers remain
- runtime gate is ready before app boot

Local shortcut for repo-backed staging:

1. Run `.\scripts\staging_ops.ps1 -Action db-up`
2. Run `.\scripts\staging_ops.ps1 -Action use-local-db`
3. Run `.\scripts\staging_ops.ps1 -Action set-access-mode -AccessMode local`
4. Run `.\scripts\staging_ops.ps1 -Action env-check`

That path gives the backend a real local Postgres instance without depending on the frontend build or a remote staging database.

For local staging, `set-access-mode -AccessMode local` also sets `AUTH_SESSION_SECURE=false` so browser/session auth works over local HTTP.

Managed Postgres shortcut:

1. set the managed `DATABASE_URL` in [.env.staging](/D:/marcc/PycharmProjects/StockMarketPredictions/.env.staging)
   - `.\scripts\staging_ops.ps1 -Action set-db-url -DatabaseUrl "postgresql+psycopg://..."`
2. inspect the target safely
   - `.\scripts\staging_ops.ps1 -Action show-db-url`
3. set real public URLs
   - `.\scripts\staging_ops.ps1 -Action set-public-urls -FrontendUrl "https://staging.example.com" -ApiBaseUrl "https://api-staging.example.com/api"`
4. set remote access mode
   - `.\scripts\staging_ops.ps1 -Action set-access-mode -AccessMode remote`
5. inspect the public URLs safely
   - `.\scripts\staging_ops.ps1 -Action show-public-urls`
6. run `.\scripts\staging_ops.ps1 -Action status`
7. run `.\scripts\staging_ops.ps1 -Action env-check`
8. run `.\scripts\staging_ops.ps1 -Action db-check`
9. inspect billing posture if needed
   - `.\scripts\staging_ops.ps1 -Action show-billing`

That path validates the database itself before you spend time debugging a full app boot.

For remote staging, `set-access-mode -AccessMode remote` restores `AUTH_SESSION_SECURE=true`.

## 3. Runtime Start

1. Start the backend through the real app process, not an ad hoc module path that skips startup behavior.
   - `.\scripts\staging_ops.ps1 -Action api`
2. Confirm startup hooks run.
3. Confirm the background worker is running as part of the app process.

Note:

- the `api` action now fails fast if the runtime gate is blocked, so unreachable database targets are caught before the app tries to boot.

## 4. Production Floor

Run:

- `.\scripts\staging_ops.ps1 -Action floor-check`

Pass condition for staging floor:

- no blocker remains for:
  - development environment
  - demo auth
  - local-demo auth provider
  - local SQLite
  - default secrets
  - stopped worker
  - missing backup or restore evidence

## 5. Personal Path

1. Run the non-chart UI acceptance checklist.
2. Record the result with `.\scripts\staging_ops.ps1 -Action ui-record -Result pass-with-chart-excluded`.

## 6. Launch Notes

- Staging is not considered ready just because it boots.
- Staging is ready when env validation, production-floor validation, and personal UI acceptance all pass.
