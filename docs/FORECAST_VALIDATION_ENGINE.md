# Forecast Validation Engine

## Purpose

Forecast Validation evaluates timestamped forecast contracts against forward-only market paths.

It is research-only. Forecast validation does not place trades, trigger paper orders, enable live trading, change broker routes, bypass risk gates, clear kill switches, grant AI order authority, mutate immutable forecast records, or automatically change ranking weights.

## Forecast Contract Source

Forecast Validation consumes forecast contracts from the forecast validation service and related research artifacts. A forecast contract is only reviewable when it was defined before the measured move and includes enough information to evaluate the forecast without hindsight.

Validation results are stored and returned separately from the original forecast contract. The original forecast record must remain immutable.

## Required Forecast Fields

Rewardable forecast contracts require:

- `prediction_id`
- `symbol`
- `prediction_created_at`
- `horizon_minutes`
- `forecast_series`
- `predicted_direction`
- `predicted_target_pct`
- `invalidation_level`
- `confidence`
- `source`

If these fields are missing, the forecast remains visible as incomplete evidence but must not contribute to forecast accuracy, forecast reward, benchmark-support, repeatability, paper-to-live, or live-readiness claims.

## Validation Requirements

Forecast Validation must evaluate only post-prediction data. Required validation evidence includes:

- aligned actual post-prediction path
- missing actual path offsets
- direction correctness
- path error
- path RMSE
- timing error
- target hit
- invalidation hit
- time to target
- max adverse excursion
- confidence calibration context
- regime or market-context label where claimed

Missing actual path data must be reported as missing data. It must not be filled from current prices, inferred from hindsight, copied from simulation, or treated as market-observed proof.

## Reward Formula

The canonical Forecast Validation v1 formula is:

```text
forecast_total_reward =
  direction_score
  + path_fit_score
  + timing_score
  - drawdown_penalty
  - volatility_mismatch_penalty
  - confidence_penalty
```

Every component should remain reviewable. Vague chart labels, visual similarity, and post-move annotations are not rewardable forecast evidence.

## Forecast Validation Hardening Plan

The report includes `forecast_validation_hardening_plan` and `aggregations.forecast_validation_hardening_plan`.

This is the proof-first hardening layer for forecast quality. It turns missing forecast-contract and actual-path evidence into explicit review items so incomplete validation cannot be mistaken for forecast edge, repeatability, benchmark support, paper-to-live readiness, or live-trading readiness.

Hardening items:

- forecast contract sample
- complete forecast contracts
- actual path coverage
- target and invalidation metrics
- calibration and regime context
- immutable validation boundary
- research-only safety boundary

The summary exposes:

- `forecast_hardening_status`
- `forecast_hardening_open_items`
- `forecast_hardening_critical_open_items`
- `top_hardening_item`
- `claim_permissions`

Claim permissions remain conservative:

- `cautious_internal_forecast_review` may become true only when evaluated forward paths exist.
- `forecast_accuracy_claim` remains false until complete forward-only actual paths and metrics support it.
- `benchmark_forecast_support` remains false until benchmark inputs can use the forecast evidence without missing critical fields.
- `automatic_ranking_mutation` remains false.
- `paper_to_live_readiness` remains false.
- `live_trading_readiness` remains false.

The hardening plan is research metadata only. It does not submit orders, trigger paper orders, enable live trading, change broker routes, bypass risk gates, clear kill switches, grant AI order authority, merge simulation evidence into real-time market-observed evidence, mutate immutable forecast records, or mutate ranking weights.

## Output Consumers

Forecast Validation may inform:

- Data Completeness Layer
- Professional Benchmark Suite
- Score Calibration and Feature Attribution
- Evidence Reward Engine
- Walk-Forward Experiment Registry
- Human vs System Shadow Mode
- Research Promotion Rules
- Proof Metrics Dashboard

Those consumers must treat Forecast Validation as evidence context only. They must not use it to approve orders, bypass blockers, loosen risk settings, change broker routes, clear kill switches, or automatically alter ranking weights.

## Safety Boundary

Forecast Validation may support manual research review. It must not:

- submit orders
- trigger paper orders
- enable live trading
- change broker routes
- bypass risk gates
- clear kill switches
- grant AI order authority
- mutate immutable forecast records
- merge simulation evidence into real-time market-observed evidence
- automatically change ranking weights

## Test Commands

```powershell
python -m pytest tests\test_forecast_validation_engine.py tests\test_forecast_validation_api.py
python -m unittest tests.test_forecast_validation_engine tests.test_forecast_validation_api
```
