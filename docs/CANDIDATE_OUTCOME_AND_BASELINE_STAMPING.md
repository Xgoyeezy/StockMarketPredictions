# Candidate Outcome And Baseline Stamping

## Purpose

Candidate Outcome and Baseline Stamping turns candidate lifecycle evidence into rewardable research proof after each forward horizon closes.

It preserves the original candidate lifecycle row and appends a separate candidate outcome row linked by `candidate_lifecycle_id`. The outcome row is research evidence only. It does not place orders, clear kill switches, change broker routes, bypass risk gates, or change ranking weights automatically.

## Evidence Files

Source:

`runtime-exports/candidate-lifecycle/<date>/<tenant>.jsonl`

Output:

`runtime-exports/candidate-outcomes/<date>/<tenant>.jsonl`

The output is append-only. Original lifecycle and forecast records are not mutated.

## Idempotency

Each stamped record uses:

`candidate_lifecycle_id + horizon_minutes + outcome_version`

The v1 outcome version is `candidate_outcome_baseline_v1`.

Available outcome rows are final for that key. Unavailable diagnostic rows are append-only observations, but they do not permanently block the same key from being rechecked. If later lifecycle evidence supplies the closed-horizon price and baseline, the service may append a final available row for the same key and reports that it supersedes the earlier unavailable diagnostics.

## Pre-Move Candidate Contract

Lifecycle capture enriches candidate rows with these fields when they can be derived honestly:

- `prediction_created_at`
- `predicted_direction`
- `prediction_horizon_minutes`
- `predicted_target_pct`
- `invalidation_level`
- `confidence`
- `engine`
- `setup_type`
- `score`
- `score_bucket`
- `regime`
- `spread_at_signal`
- `slippage_estimate_bps`
- `route`
- `experiment_version`
- `reward_formula_version`
- `baseline_definition_version`
- `feature_version`
- `sample_split`

If a field cannot be derived honestly, it stays missing and the row remains non-rewardable until the missing evidence exists.

## Forward Outcomes

V1 stamps matured horizons only:

- `5m`
- `15m`
- `30m`
- the candidate's declared `prediction_horizon_minutes`

Computed fields:

- `actual_forward_return`
- `actual_forward_return_observed_at`
- `max_adverse_excursion`
- `hit_target`
- `hit_invalidation`
- `time_to_target_minutes`

The service uses later real-time market-observed price evidence from candidate lifecycle artifacts. It does not use current price as a substitute for a closed horizon. If closed-horizon price evidence is missing, the record reports `available: false`, `missing_fields`, and a reason.

## Baselines

Baselines are transparent and only available when the matching timestamp and horizon can be computed:

- `spy_forward_return`
- `qqq_forward_return`
- `sector_etf_forward_return`
- `random_candidate_forward_return`
- `simple_momentum_forward_return`
- `simple_mean_reversion_forward_return`
- `simple_vwap_reclaim_forward_return`
- `opening_range_breakout_forward_return`
- `previous_close_drift_forward_return`

Primary baseline:

1. `random_candidate_forward_return` when available
2. `spy_forward_return` otherwise

Missing baselines are not fabricated.

## Execution Cost Evidence

Execution cost fields come from paper-route evidence only:

- `spread_at_signal`
- `quote_freshness_seconds`
- `expected_cost_estimate_bps`
- `order_id`
- `trade_id`
- `intended_price`
- `fill_price`
- `slippage_bps`
- `fill_delay_seconds`
- `partial_fill`
- `paper_fill_status`

No order routing or order behavior changes.

## API Endpoints

- `GET /api/evidence-outcomes/summary`
- `GET /api/evidence-outcomes/due`
- `GET /api/evidence-outcomes/records`
- `POST /api/evidence-outcomes/stamp-due`

The POST endpoint writes append-only research evidence only and returns:

- `research_only: true`
- `paper_only: true`
- `can_submit_orders: false`
- `can_submit_live_orders: false`

## Analytics Consumers

The existing analytics stack consumes stamped outcomes through Evidence Reward:

- Professional Benchmark Suite
- Data Completeness Layer
- Score Calibration and Feature Attribution
- Execution Quality and TCA
- Walk-Forward Experiment Registry
- Research Promotion Rules
- Human vs System Shadow Mode
- Portfolio Risk Intelligence

Forecast Validation remains immutable. Validation records stay separate from original forecasts.

## Continuous Ops

Continuous Ops attempts safe stamping after candidate lifecycle capture and during its normal watchdog loop. It stamps only matured horizons, is idempotent, and reports failures as warnings only.

It never clears kill switches, changes readiness, submits orders, changes broker routes, changes risk gates, or changes ranking weights.

## Missing Data Behavior

Missing data produces explicit diagnostics:

- `available: false`
- `missing_fields`
- `reason`

The service does not fabricate returns, baselines, sectors, execution costs, or regimes.

Repeated stamping does not rewrite the same unavailable diagnostic when nothing changed. It rechecks matured horizons, skips duplicate unavailable writes, and only appends again when the missing evidence becomes sufficient for a final available outcome row.

## Test Commands

```powershell
python -m pytest tests\test_candidate_outcome_stamping_service.py
python -m pytest tests\test_candidate_outcome_stamping_service.py tests\test_evidence_reward_engine_service.py tests\test_professional_benchmark_suite_service.py -q
```

## UI Route

`/evidence-outcomes`

The page shows due horizons, stamped records, rewardability lift, baseline coverage, execution-cost coverage, missing data, last run state, and safety notes.

## Safety Boundary

This layer is research/evidence only.

It does not:

- enable live trading
- submit paper or live orders
- change broker routes
- change order submission logic
- change risk gates
- clear kill switches
- let AI place orders
- automatically change ranking weights
- merge simulation evidence into real-time market-observed evidence
