# Incident Response Runbook

## Goal
Stabilize the platform quickly when a release, dependency, or provider issue impacts tenants.

## Triage
1. Check `/api/health`.
2. Open the release center and review:
   - request latency
   - dead-letter jobs
   - deployment blockers
   - backup posture
3. Identify whether the issue is:
   - deployment/runtime
   - external provider
   - billing/auth
   - market data

## Immediate actions
- Pause risky operator actions if the system is unstable.
- Stop repeated retries only if they are making the incident worse.
- Capture timestamps, affected tenants, and the first visible symptom.

## Recovery
- Use the rollback runbook if the incident started right after a deployment.
- Use the backup and restore runbook if runtime state is corrupted.
- Record follow-up tasks for missing monitoring or guardrails.
