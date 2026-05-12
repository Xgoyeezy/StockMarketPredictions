# Risk Gate And Audit Trail Hardening

## Purpose

Risk Gate and Audit Trail hardening is a read-only proof report for the control plane. It checks whether risk policies, risk events, kill-switch actions, audit events, decision replay evidence, audit exports, and safety ledger summaries are visible enough to support cautious internal review.

It does not approve risk settings, clear kill switches, authorize live trading, change broker routes, submit orders, or change ranking weights.

## Hardening Report

The report is served from:

```text
GET /api/risk/audit-hardening
```

It returns:

- `summary`
- `risk_policies`
- `risk_events`
- `audit_events`
- `audit_exports`
- `trade_replays`
- `risk_audit_hardening_plan`
- `warnings`
- `safety_notes`
- `finish_tracker`

Every response carries explicit false authority flags:

- `can_submit_orders: false`
- `can_submit_live_orders: false`
- `can_change_broker_routes: false`
- `can_bypass_risk_gates: false`
- `can_clear_kill_switch: false`
- `can_change_ranking_weights: false`
- `mutation: "none"`

## Hardening Items

The plan tracks:

- Active risk policy evidence
- Risk event lineage
- Kill-switch auditability
- Audit event lineage
- Decision replay traceability
- Sanitized export boundary
- Safety ledger visibility
- Read-only governance boundary

Each item reports status, priority, metric value, missing evidence, blocked claims, and a safe next action.

## Kill-Switch Auditability

Kill-switch activation and clear operations now write audit events:

- `risk.kill_switch_activated`
- `risk.kill_switch_cleared`

Those events include:

- actor email
- reason
- strategy or tenant scope
- affected count
- timestamp

This improves future traceability. It does not clear the kill switch or loosen any risk gate by itself.

## Claim Boundaries

The hardening plan blocks these claims unless evidence is complete:

- risk gate authority claim
- audit completeness claim
- kill-switch recovery claim
- paper-to-live readiness
- broker route safety claim
- compliance approval claim
- live-trading readiness

Even when every hardening item is ready, the only permission granted is cautious internal risk/audit review. The plan still does not grant live trading, compliance approval, automatic execution mutation, broker-route changes, kill-switch clearance, or risk-gate changes.

## UI Route

The Risk Center shows the hardening report at:

```text
/risk
```

The page includes:

- hardening status
- critical blockers
- internal review permission
- blocked claims
- kill-switch audit event count
- hardening item table
- warnings and safety boundaries
- the project-wide finish tracker at the end

## Verification

Focused verification commands:

```powershell
backend\.venv\Scripts\python.exe -m pytest -q tests\test_risk_audit_hardening.py tests\test_risk_audit_hardening_frontend_static.py tests\test_audit_replay_api.py tests\test_risk_control_service.py
```

Frontend build check:

```powershell
cd frontend
cmd /c "set NODE_OPTIONS=--max-old-space-size=4096&& npm.cmd run build"
```

Runtime probe after restart:

```powershell
curl.exe -i -s http://127.0.0.1:8000/api/risk/audit-hardening
```

## Limitations

- Historical kill-switch operations before this change may not have audit events.
- The report cannot invent missing risk events, audit events, replay snapshots, or safety ledger records.
- Safety ledger visibility depends on the trading safety service writing daily state.
- Sanitized export proof depends on audit exports being queued through the control plane.
- This is not legal, compliance, investment, or live-trading approval.
