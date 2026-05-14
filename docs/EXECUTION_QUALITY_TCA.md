# Execution Quality And Transaction Cost Analysis

## Purpose

Execution Quality and Transaction Cost Analysis v1 measures whether correct forecasts and good paper candidates are actually tradable after spread, slippage, fill delay, partial fills, missed fills, and alpha decay. It is analytics only and does not change order routing or order behavior.

## Paper-Only Boundary

The service only reports paper-route evidence. Rows that clearly look like live-route evidence or simulation evidence are excluded. Every response includes:

- `research_only: true`
- `paper_only: true`
- `can_submit_orders: false`
- `can_submit_live_orders: false`
- `mutation: "none"`
- `execution_quality_hardening_plan`

The layer cannot enable live trading, submit orders, change broker routes, bypass risk gates, clear kill switches, grant AI order authority, change routing automatically, or change ranking weights automatically.

## Data Requirements

Core fields:

- `order_id` or `trade_id`
- `linked_candidate_id` when available
- `symbol`
- `timestamp`
- `engine`
- `setup_type`
- `route`
- `expected_entry_price`, `expected_price`, `submitted_price`, or `intended_price`
- `actual_fill_price`, `fill_price`, or `filled_price`
- `spread_at_signal` or `spread_bps`
- submitted and filled timestamps, explicit fill delay, or latency

Optional fields:

- `actual_forward_return`
- `baseline_forward_return`
- `total_reward`
- `alpha_at_signal`
- `alpha_after_fill`
- `quote_age_seconds`
- `liquidity_score`
- partial-fill quantities
- paper fill status

Missing fields are reported instead of inferred.

## Metrics And Formulas

`slippage_bps`

```text
((fill_price - intended_price) / intended_price) * 10000
```

Explicit slippage fields are used first when present.

`spread_cost`

```text
max(0, spread_at_signal_bps)
```

`fill_delay_seconds`

Uses explicit delay first, then latency milliseconds, then `filled_at - submitted_at`.

`alpha_decay`

```text
alpha_at_signal - alpha_after_fill
```

If those are missing, the service uses `expected_forward_return - actual_forward_return` when both are present.

`execution_adjusted_reward`

```text
total_reward - abs(slippage_bps) / 100 - spread_bps / 100
```

An explicit execution-adjusted reward is used first when present.

`cost_adjusted_edge`

```text
(actual_forward_return - baseline_forward_return)
- ((abs(slippage_bps) + spread_bps) / 100)
```

`execution_quality_score`

Starts at 100 and applies transparent penalties for cost drag, fill delay, missed-fill rate, and partial-fill rate.

## Aggregations

The report computes:

- average slippage
- median slippage
- slippage by engine
- slippage by setup type
- slippage by symbol
- slippage by regime
- fill delay by engine
- alpha decay by engine
- execution-adjusted reward by setup
- spread cost by setup
- missed-fill rate
- partial-fill rate
- execution-quality score

## Execution Proof Gate

The service emits a read-only `proof_summary` in the summary response and under `aggregations.execution_proof`. This proof gate is for human research review only. It is not proof of alpha, guaranteed returns, investor performance, live-trading readiness, institutional readiness, HFT capability, or permission to change routes.

Proof requirements:

- Paper execution sample: enough paper-route execution rows exist.
- Cost evidence coverage: rows include slippage, spread, and fill-delay evidence.
- Execution-adjusted reward: reward remains positive after spread and slippage drag.
- Cost-adjusted edge: same-window baseline-relative edge survives execution costs.
- Fill quality: missed, rejected, canceled, expired, and no-fill rows remain under the configured threshold.
- Candidate and route linkage: rows link paper order evidence to candidates, explicit paper route evidence, and fills.
- Paper-only safety boundary: TCA stays read-only paper-route analytics and cannot alter routes or submit orders.

The proof response includes:

- `status`
- `proof_ready`
- `requirements`
- `summary`
- `record_readiness`
- `safe_next_actions`
- safety notes and mutation flags

Every requirement row includes flags showing it does not change execution, order submission, broker routes, risk gates, or ranking weights.

## Execution Quality Hardening Plan

The service also emits `execution_quality_hardening_plan`, a proof-first work queue for the paper execution layer. It turns missing TCA proof into explicit claim boundaries instead of treating row count or high-level execution score as tradability proof.

The hardening plan tracks:

- paper execution sample
- cost evidence capture
- candidate and route linkage
- execution-adjusted reward
- cost-adjusted edge
- fill quality
- paper-only governance

Each item reports priority, status, linked proof keys, missing fields, blocked claims, a safe next action, and a `done_when` condition. The plan also returns `claim_permissions` so the UI can show that public execution-quality claims, tradability claims, route changes, broker-route changes, automatic execution mutation, paper-to-live readiness, and live-trading readiness remain blocked.

Hardening plan items are internal paper-route research gates only. They do not fabricate fills, infer broker evidence, submit orders, change routes, change broker settings, bypass risk gates, or approve live trading.

## API Endpoints

All paths are under the configured API prefix, usually `/api`.

- `GET /api/execution-quality/summary`
- `GET /api/execution-quality/trades`
- `GET /api/execution-quality/slippage`
- `GET /api/execution-quality/alpha-decay`
- `GET /api/execution-quality/engines`
- `GET /api/execution-quality/setups`

## UI Route

- `/execution-quality`

The page shows paper-only TCA metrics, average slippage, fill delay, alpha decay, execution-adjusted reward, best/worst execution setups, spread cost, partial-fill rate, missed-fill rate, warnings, and missing fields.

The page also shows the Execution Proof Gate, Execution Quality Hardening Plan, and Execution Record Readiness table so operators can see why tradability-after-costs evidence is or is not ready for human review and which claims remain blocked.

## How This Supports The Benchmark Suite

Forecast and reward evidence only proves a decision was directionally useful. TCA checks whether the decision survived real paper execution costs. Benchmark, reward, and promotion reports should treat execution-adjusted evidence as stronger than raw forecast correctness.

## Safety Notes

Execution Quality TCA does not:

- submit orders
- route orders
- cancel orders
- repair broker orders
- change broker routes
- enable live trading
- bypass risk gates
- clear kill switches
- change ranking weights automatically
- merge simulation evidence into real-time market-observed evidence

## Test Commands

```powershell
python -m compileall -q backend tests scripts
python -m unittest tests.test_execution_quality_tca tests.test_api_route_health
python -m unittest tests.test_professional_benchmark_suite_service tests.test_evidence_reward_engine_service tests.test_execution_quality_tca tests.test_api_route_health
npm.cmd run build
```

Run the frontend build from:

```powershell
cd frontend
```

## Limitations

- v1 is descriptive analytics, not causal proof.
- Slippage sign is preserved; dashboards should inspect both signed and absolute cost.
- Missing fill timestamps, intended prices, or fill prices reduce TCA confidence.
- Raw broker records, account IDs, raw logs, and raw local paths are not exposed.

## Candidate Outcome Source

Candidate Outcome and Baseline Stamping attaches paper-route execution cost evidence to candidate outcomes when available: spread at signal, quote freshness, expected cost estimates, fill prices, slippage, fill delay, partial fill state, and paper fill status.

Execution Quality and TCA remains read-only analytics. It does not change routing, place orders, clear blockers, alter broker routes, or automatically change ranking weights.

## Future Off-Exchange And Broker-Neutral Context

Future Off-Exchange Liquidity Dashboard inputs may support Execution Quality and TCA by adding passive research context for FINRA OTC transparency data, ATS volume, non-ATS off-exchange volume, symbol-level off-exchange share, venue concentration where available, off-exchange activity spikes, spread and liquidity quality, slippage by off-exchange share, and candidate outcomes in high vs low off-exchange regimes.

This future context must not act on trades, trigger trades, block trades automatically, change routes, or change ranking weights automatically. It must not claim the system sees hidden orders, knows institutional intent, or that dark pool prints predict direction.

Future broker-neutral execution architecture may route execution evidence through broker adapters, but Execution Quality and TCA remains analytics. A broker adapter can provide receipts, fills, positions, clock, account, and buying-power data for review. The broker still owns custody, regulated account infrastructure, market access, routing, clearing, and broker compliance. Quant Evidence OS owns evidence, reconciliation, analytics, audit trail, and candidate diagnostics.
