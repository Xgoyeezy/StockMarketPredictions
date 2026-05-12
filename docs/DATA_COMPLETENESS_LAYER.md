# Data Completeness Layer

## Purpose

The Data Completeness Layer is a research-only diagnostics surface for Quant Evidence OS. It audits whether candidate, forecast, AI verdict, blocker, missed-move, paper trade, execution-quality, and benchmark records contain enough forward-known evidence to support reward analytics and professional benchmark claims.

It does not trade. It does not change broker routes. It does not bypass risk gates. It does not change ranking weights automatically.

## Research-Only Boundary

Every Data Completeness response carries these safety flags:

- `research_only: true`
- `can_submit_orders: false`
- `can_submit_live_orders: false`
- `mutation: "none"`

Completeness findings may inform manual research and engineering work. They must not trigger trades, clear kill switches, change broker routes, loosen risk gates, mutate ranking weights, or grant AI order authority.

Simulation evidence remains separate from real-time market-observed evidence and is not treated as rewardable live evidence.

## Institutional Data Lineage Assumptions

Institutional and enterprise-control-plane readiness requires explicit data lineage. Data Completeness records should identify the vendor or source, source version, `as_of` time, effective time, observed time, feature generation time when applicable, universe version, and whether the row is eligible for point-in-time evaluation.

These fields are review evidence only. Missing lineage must block stronger claims, not trigger trades, alter ranking weights, change broker routes, or loosen risk gates.

### Data Lineage Guide

Data lineage connects each forecast, candidate, benchmark row, walk-forward split, reward output, and promotion review back to the data snapshot used at decision time. A reviewer should be able to inspect vendor provenance, source version, point-in-time timestamps, universe version, corporate-action assumptions, and feature-generation timestamps without relying on verbal explanation.

### Point-In-Time Data

Point-in-time evaluation requires data that was known at or before the forecast, scan, benchmark, or walk-forward decision time. Required fields include:

- `as_of`
- `effective_at`
- `observed_at`
- `source_version`
- `no_lookahead`

Rows that cannot prove point-in-time availability remain visible as incomplete institutional evidence.

### Survivorship-Free Universe

Long-horizon and institutional-style research should use a universe snapshot that includes active and delisted symbols for the relevant `as_of_date`. Required fields include:

- `universe_id`
- `as_of_date`
- `active_symbols`
- `delisted_symbols`
- `membership_source`
- `survivorship_free`

If the universe is not survivorship-free, the report must say so plainly before any benchmark or institutional-readiness claim.

### Corporate Actions And Symbol Changes

Corporate actions and symbol changes must be handled by a data vendor or explicitly documented as an assumption. Required fields include:

- `symbol`
- `as_of_date`
- `corporate_actions_policy`
- `symbol_change_policy`
- `adjustment_source`

Acceptable handling can include adjusted-price references, raw-price references, split/dividend adjustment metadata, and symbol-master mappings from old identifiers to current identifiers. The system must not silently treat undocumented symbol history as institutional-grade evidence.

### Data Vendor Provenance

Vendor provenance should identify the vendor or source, source version, license or contract reference, `as_of` time, and receipt time. Vendor provenance is used to make research reviewable and reproducible; it must not expose secrets, account identifiers, raw vendor records, raw broker records, raw logs, or raw local paths.

### Feature Generation Timestamps

Feature rows should identify the feature id, feature version, generation timestamp, source `as_of` timestamp, and no-lookahead status. Missing feature timestamps should block institutional-readiness language because a reviewer cannot prove whether a feature was available before the forecast, benchmark, walk-forward split, or outcome.

### Model Lineage Guide

Model lineage connects a forecast model or score model to its model id, model version, training data version, feature version, creation timestamp, approval record, benchmark run, and walk-forward experiment. Model lineage is research and governance evidence only; it must not automatically promote a model, change ranking weights, alter execution behavior, or bypass risk gates.

## Contracts

### Candidate Reward Contract

Required fields:

- `symbol`
- `timestamp` or `prediction_created_at`
- `engine`
- `setup_type`
- `score`
- `allowed` or `blocked`
- `blockers`
- `actual_forward_return`
- `baseline_forward_return`

Candidate records without explicit forward return and baseline fields remain visible, but they are not considered complete for reward or benchmark attribution.

### Forecast Validation Contract

Required fields:

- `prediction_id`
- `symbol`
- `prediction_created_at`
- `horizon_minutes`
- `forecast_series`
- `predicted_direction`
- `predicted_target_pct`
- `invalidation_level`
- `confidence`
- `actual_series`
- `actual_forward_return`
- `baseline_forward_return`

Forecast overlays must be immutable prediction contracts. Validation data is evaluated after `prediction_created_at` and stored separately from the original forecast record.

### Execution Quality Contract

Required fields:

- `symbol`
- `timestamp`
- `order_id` or `trade_id`
- `intended_price`
- `fill_price`
- `spread_at_signal`
- `slippage`
- `fill_delay`
- `route`
- `paper_fill_status`

Execution completeness supports slippage-adjusted reward and benchmark analysis. It does not change routing.

### AI Review Contract

Required fields:

- `symbol`
- `timestamp`
- `ai_verdict`
- `confidence`
- `reason`
- `linked_candidate_id` or `evidence_id`
- `actual_outcome`

AI can review, classify, and critique evidence. AI cannot place orders, clear gates, change ranking weights, or override reconciliation.

### Missed Move Contract

Required fields:

- `symbol`
- `timestamp`
- `blocked_reason`
- `forward_return`
- `baseline_forward_return`
- `move_magnitude`
- `recoverable_flag`

Missed-move completeness explains whether blocked opportunities can be evaluated as correct blocks, bad misses, or insufficient evidence.

## Rewardability Rules

A record is complete when every required field for its source contract is present. Complete records are rewardable unless they are explicitly marked as simulation evidence. Incomplete records stay visible with:

- `complete: false`
- `rewardable: false`
- `missing_fields`
- `reason`
- `warnings`

The layer does not fabricate missing data, use current prices as a forward return substitute, infer baselines from hindsight, or reward vague visual labels.

## Aggregations

The summary report computes:

- `total_records`
- `complete_records`
- `incomplete_records`
- `rewardable_records`
- `non_rewardable_records`
- `completion_rate`
- `rewardability_rate`
- `missing_field_counts`
- `missing_by_source`
- `missing_by_engine`
- `missing_by_setup_type`
- `missing_by_regime`
- `highest_priority_missing_fields`
- `benchmark_blockers`
- `cleanup_plan_status`
- `cleanup_plan_open_items`
- `cleanup_plan_critical_open_items`
- `top_cleanup_item`

Benchmark blockers highlight fields that most directly prevent Professional Benchmark Suite proof, such as forward returns, baseline returns, forecast paths, actual paths, slippage, and spread fields.

## Proof Field Coverage

The summary now includes `proof_field_coverage`, a research-only readiness block for the roadmap fields that must be present before benchmark or walk-forward proof can support stronger claims:

- forward returns
- baseline returns
- forecast actuals
- execution costs
- regime labels
- required reward fields

`summary.benchmark_ready` requires both base benchmark evidence and proof-field coverage. Missing proof fields create manual safe next actions only. They do not place orders, change broker routes, bypass risk gates, alter ranking weights, or infer missing market outcomes.

## Data Cleanup Plan

The summary includes `data_cleanup_plan`, an ordered manual work queue for the proof-first cleanup tasks in `docs/PROOF_FIRST_ROADMAP_DISCIPLINE.md`:

- missing forward returns
- missing baselines
- missing forecast actuals
- missing spread, slippage, fill delay, route, and paper-fill evidence
- missing setup, engine, regime, timestamp, and horizon context
- missing reward contract fields

Each cleanup item reports status, priority, affected sources, accepted fields, missing counts, blocked reports, a safe next action, and a `done_when` condition. Cleanup items are diagnostic only: they do not fabricate market outcomes, submit orders, change broker routes, bypass risk gates, or mutate ranking weights.

## API Endpoints

Read-only endpoints:

- `GET /api/data-completeness/summary`
- `GET /api/data-completeness/candidates`
- `GET /api/data-completeness/forecasts`
- `GET /api/data-completeness/ai`
- `GET /api/data-completeness/blockers`
- `GET /api/data-completeness/execution`
- `GET /api/data-completeness/benchmark-readiness`

Every endpoint returns:

- `status`
- `generated_at`
- `research_only`
- `summary`
- `records`
- `aggregations`
- `proof_field_coverage`
- `data_cleanup_plan`
- `missing_fields`
- `warnings`
- `safety_notes`

## UI Route

Frontend route:

- `/data-completeness`

The page shows completion rate, rewardability rate, benchmark readiness, proof-field coverage, the manual data cleanup plan, missing fields by category, category-level contract coverage, and safe next actions. The UI copy explicitly states that the page is research-only and does not affect trading.

## How This Supports Benchmarks

Professional Benchmark Suite v1 can only answer whether Quant Evidence OS beat simple baselines after costs when required evidence exists. Data Completeness identifies the missing fields that prevent that proof:

- forward outcomes
- baseline outcomes
- forecast and actual paths
- spread and slippage fields
- execution timing
- AI outcome links
- missed-move recoverability labels

This turns weak benchmark sections into concrete data-capture tasks.

## Known Limitations

- The layer audits available local evidence and service reports; it does not add a new database.
- Missing data is reported honestly instead of inferred.
- Execution-quality records may require existing entitlement-gated services to be available.
- Legacy evidence may remain incomplete until future candidate lifecycle rows emit full prediction and outcome fields.

## Test Commands

From the repository root:

```powershell
python -m compileall -q backend tests scripts
python -m unittest tests.test_data_completeness_audit_service tests.test_api_route_health
python -m unittest tests.test_professional_benchmark_suite_service tests.test_evidence_reward_engine_service tests.test_forecast_validation_engine tests.test_forecast_validation_api
cd frontend
npm.cmd run build
```

## Candidate Outcome Source

Data Completeness treats Candidate Outcome and Baseline Stamping as the source for candidate rewardability blockers. Stamped records live in `runtime-exports/candidate-outcomes/<date>/<tenant>.jsonl` and remain separate from original lifecycle and forecast records.

The Data Completeness page shows compact outcome readiness cards for due horizons, stamped outcomes, baseline coverage, and execution-cost coverage. Missing outcome fields are diagnostic only and never change execution, risk gates, broker routes, or ranking weights automatically.
