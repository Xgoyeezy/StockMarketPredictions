# Own-Account Intraday Implementation Checklist

Use this checklist before treating the intraday trading workflow as operationally ready. It is an operator runbook, not a trading signal and not permission to bypass risk controls.

## Pre-Open

- Backend `/api/healthz` returns `ok`.
- Backend `/api/readyz` has no blocking deployment, safety, or route issues.
- Frontend is reachable at the configured local URL.
- Alpaca paper credentials are present and the active unattended route is `broker_paper`.
- Market Watchdog is reachable and reports a concrete state.
- Kill switch is off before arming; if it is active, investigate the blocker and clear it only through the explicit operator path.
- Paper reconciliation is clean: no orphan broker events, no stale pending orders, and no duplicate client-order conflict.
- Risk caps are present: daily loss budget, notional caps, cooldown, open heat, and objective lock behavior.
- Active desks are enabled and have expected scan windows.

## Active Session

- Worker heartbeat advances and is not stale.
- Due desks scan within their freshness SLA.
- Candidate diagnostics refresh during the market window.
- Deep analysis and rapid confirmation queues move or report a clear blocker.
- No-trade checkpoints at 10:30, 12:00, and 14:00 produce diagnostic evidence if no trades occur.
- Evidence Reward and Forecast Validation remain research-only and do not affect order flow.
- AI evidence review remains advisory and cannot place orders or override risk gates.

## Paper Order Readiness

- Every candidate must pass quote freshness, spread, routeability, cooldown, risk, reconciliation, and kill-switch gates.
- Paper orders must use Alpaca paper execution only.
- Order evidence must include candidate context, risk gate result, receipt, and reconciliation state.
- Manual operator notes may explain a decision but cannot override risk gates.

## Close And Review

- Close report is generated after the session.
- Daily ledger, no-trade report, candidate diagnostics, and market-day report are available.
- Missed-move and blocker evidence are reviewed as research, not as automatic ranking changes.
- Any runtime issue is added to incident notes before the next session.

## Non-Negotiable Boundaries

- No autonomous live-money orders.
- No broker-route loosening.
- No risk-gate bypass.
- No automatic kill-switch clearing.
- No AI order authority.
- No reward or forecast output may trigger trades.
