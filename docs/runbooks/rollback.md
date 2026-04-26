# Rollback Runbook

## Goal
Return the platform to the last known good state when a deployment causes regressions.

## Preconditions
- A recent backup exists.
- The current failure mode has been confirmed to be release-related.

## Steps
1. Identify the last known good deployment timestamp.
2. Stop the current deployment if it is still rolling out.
3. Restore the prior image, build, or code version.
4. If runtime data changed during the bad release, follow the backup and restore runbook.
5. Start the stack and verify:
   - `GET /api/health`
   - frontend loads
   - jobs are not piling up
   - no new billing or auth errors appear

## After rollback
- Record the incident.
- Update the release center blockers if follow-up work is required.
- Do not redeploy until the root cause is understood.
