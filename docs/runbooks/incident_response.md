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
   - proof evidence
   - safety boundary

## Immediate actions
- Pause risky operator actions if the system is unstable.
- Stop repeated retries only if they are making the incident worse.
- Capture timestamps, affected tenants, and the first visible symptom.
- Capture affected proof surfaces, such as Data Completeness, Professional Benchmark, Walk-Forward, Execution Quality, Score Calibration, Risk Gate and Audit Trail, Portfolio Risk, Human vs System, Research Promotion, Evidence Reward, or Forecast Validation.
- Confirm whether execution, broker routes, order logic, risk gates, kill switches, AI authority, ranking weights, or simulation separation were affected.
- Keep incident notes sanitized. Do not paste secrets, broker records, account identifiers, raw logs, raw local paths, database files, credentials, or environment values.

## Recovery
- Use the rollback runbook if the incident started right after a deployment.
- Use the backup and restore runbook if runtime state is corrupted.
- Record follow-up tasks for missing monitoring or guardrails.
- Verify the affected proof surfaces before closure.
- Record closure evidence with severity, owner, containment, corrective action, verification performed, post-incident review note, and blocked redeploy condition if applicable.

## Safety Boundary
- Incident records are review metadata only.
- Incident response must not clear kill switches, bypass risk gates, change broker routes, change order behavior, grant AI order authority, mutate ranking weights, approve live-money autonomy, or treat incomplete proof as passing proof.
