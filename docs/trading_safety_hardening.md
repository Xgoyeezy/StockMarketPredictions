# Trading Safety Hardening

This system is positioned as an Alpaca-paper-first trading control plane. Unattended execution must fail closed, stay on the `broker_paper` route, and prove why every scan, block, order intent, and paper receipt happened.

The customer-facing operating surface is now the Market Watchdog. It answers whether the system is alive, connected, scanning, reconciled, and allowed to trade. The older safety-state services remain the source of truth behind that surface.

## Service Boundaries

- `backend/services/trading_safety_service.py` is the account-level safety source of truth. It evaluates route enforcement, market-session state, objective and loss locks, stale order/position guardrails, last-known-safe snapshots, ledger summaries, and HFT artifact status.
- `backend/services/trade_automation_service.py` owns multi-desk scheduling, candidate diagnostics, desk runtime counters, allocator evidence, and paper-cycle decisions. It does not loosen filters to chase the 1% objective.
- `backend/services/alpaca_paper_readiness_service.py` owns non-secret Alpaca paper readiness evidence: credential presence, local order reconciliation, duplicate client-order-id guards, rejected-order normalization, latency summaries, and paper-account mode assertions.
- `backend/services/evidence_reward_engine.py` owns research-only prediction-contract reward analytics. It never changes execution, ranking weights, routes, risk gates, or kill-switch state.
- `hft_system/hft/millisecond/watchdog.py` owns supervised millisecond paper slices. It records run artifacts, process locks, heartbeat age, dry-run/paper-submit mode, symbol-set hash, latency percentiles, and near-close no-new-order proof.

## Public Safety APIs

- `GET /api/orgs/trade-automation/safety-state`
- `GET /api/orgs/trade-automation/watchdog`
- `GET /api/orgs/trade-automation/market-session`
- `GET /api/orgs/trade-automation/daily-ledger`
- `GET /api/orgs/trade-automation/daily-safety-summary`
- `GET /api/orgs/trade-automation/alpaca-paper-readiness`
- `GET /api/orgs/trade-automation/hft-watchdog/latest`
- `GET /api/orgs/trade-automation/desks`
- `GET /api/orgs/trade-automation/desks/{desk_key}/candidate-diagnostics`
- `GET /api/orgs/trade-automation/evidence-reward/summary`

All endpoints return `{ ok, data, meta }` envelopes through FastAPI routers and must not expose secret values.

## Safety Ledger

Daily safety ledger rows live under:

```text
runtime/trading-safety/YYYY-MM-DD.jsonl
```

The raw ledger is append-only for the day. Compaction writes a summary beside the source file and preserves the JSONL:

```powershell
python scripts/trading_safety_tools.py compact --tenant-slug systematic-equities
python scripts/trading_safety_tools.py summary --tenant-slug systematic-equities
```

## Market-Ready Report

Use the local market-ready command before unattended paper operation:

```powershell
python scripts/trading_safety_tools.py market-ready --env-file .env --tenant-slug systematic-equities
```

The report combines env checks, latest safety state, daily summary, HFT watchdog status, route-table health, artifact index metadata, and weak/strong scan findings. It writes:

```text
runtime-exports/trading_safety_validation_summary.json
```

## Operator UI

The visible customer/operator surface should show:

- one Market Watchdog banner state: `Ready`, `Watching`, `Needs attention`, `Blocked`, or `Killed`;
- an Alpaca paper-only marker;
- per-desk blockers, next scans, and safe-to-trade state;
- candidate diagnostics and why-no-trade evidence;
- HFT watchdog status and latest child-run evidence;
- daily ledger and diagnostics links.
- Evidence Reward as a research-only section that separates rewardable prediction contracts from incomplete evidence.

Internal implementation detail should remain hidden unless it blocks the next safe action.

## Invariants

- Alpaca paper is the only unattended execution route.
- Buying power is a ceiling, not the sizing base.
- The +1% daily objective is not a guarantee and never forces trades.
- A +1% objective lock blocks new entries globally.
- A -0.5% daily loss lock blocks new entries globally.
- Signals cannot submit directly to a broker route.
- Live-money autonomy remains disabled unless a separate explicit live-control workflow passes every authorization and risk gate.
- Evidence Reward cannot submit orders, mutate ranking weights, clear kill switches, or treat simulation evidence as live-observed edge.
- Visual chart labels do not count as rewarded edge unless tied to a timestamped prediction contract with direction, horizon, target, invalidation, confidence, actual outcome, and baseline outcome.
