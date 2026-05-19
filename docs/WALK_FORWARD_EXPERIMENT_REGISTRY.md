# Walk-Forward Experiment Registry

## Purpose

The Walk-Forward Experiment Registry freezes research configurations so Quant Evidence OS can evaluate whether signals, forecasts, blockers, and rankings keep working after rules are locked.

This is research metadata only. It does not change trading behavior, broker routes, order submission logic, risk gates, kill switches, AI authority, ranking weights, risk settings, or live-trading state.

## Research-Only Boundary

Registry writes are limited to sanitized experiment metadata under the local research artifact store. The registry does not mutate:

- execution configuration
- broker settings
- live or paper order behavior
- risk configuration
- ranking weights
- AI authority

Every response includes:

- `research_only: true`
- `can_submit_orders: false`
- `can_submit_live_orders: false`
- `mutation: "research_metadata_only"`
- `writes_execution_config: false`
- `writes_broker_config: false`
- `writes_risk_config: false`
- `writes_ranking_config: false`

## Experiment Lifecycle

Supported statuses:

- `draft`
- `frozen`
- `running`
- `completed`
- `rejected`
- `needs_more_evidence`

Rules:

- `draft` can be edited by service-level metadata helpers.
- `frozen` cannot change parameters.
- `running` cannot change parameters.
- `completed` cannot change parameters.
- `rejected` cannot change parameters unless cloned.
- `needs_more_evidence` cannot change parameters unless cloned.

If parameters need to change after freezing, clone the experiment and create a new draft version.

## Frozen-Parameter Rule

Each experiment snapshots:

- `experiment_id`
- `name`
- `description`
- `created_at`
- `created_by`
- `status`
- `train_window`
- `validation_window`
- `test_window`
- `paper_forward_window`
- `strategy_config_version`
- `risk_config_version`
- `ranking_formula_version`
- `reward_formula_version`
- `forecast_model_version`
- `baseline_definition_version`
- `feature_version`
- `market_universe`
- `data_source`
- `code_version`
- `frozen_parameters`
- `allowed_change_policy`
- `metrics`
- `warnings`

The registry computes a `parameter_digest` from the sanitized frozen parameter snapshot. This is a research checksum, not a trading gate.

## Data Windows

Walk-forward experiments separate:

- `train_window`: historical period used to inspect or define research rules.
- `validation_window`: period used to check the selected rules before test.
- `test_window`: forward-only out-of-sample proof window.
- `paper_forward_window`: future paper-observation window after the experiment is frozen.

The registry stores the windows. It does not fetch market data or route orders.

## Verdict Rules

V1 maps Professional Benchmark Suite verdicts into experiment verdicts:

- `edge_detected` with enough rewardable rows maps to `passed`.
- `weak_edge_detected` maps to `weak_pass`.
- `no_edge_detected` maps to `failed`.
- `insufficient_evidence` maps to `insufficient_evidence`.
- `data_quality_too_weak` maps to `data_quality_too_weak`.

The registry intentionally does not overstate results. If sample size is too small, the experiment verdict stays `insufficient_evidence`.

## Evaluation Output

Experiment metrics include:

- `sample_size`
- `rewardable_count`
- `non_rewardable_count`
- `baseline_relative_edge`
- `score_bucket_lift`
- `forecast_accuracy`
- `blocker_value`
- `ai_verdict_accuracy`
- `execution_adjusted_reward`
- `regime_stability`
- `max_drawdown`
- `profit_factor`
- `warnings`
- `verdict`

These metrics are copied from research reports and benchmark snapshots. They do not update live or paper execution policy.

## Walk-Forward Proof Gate

The registry now emits a read-only `proof_summary` on summary, list, get, create, freeze, and clone responses. This proof gate is for human research review only and is not proof of alpha, guaranteed returns, investor performance, institutional readiness, HFT capability, or live-trading readiness.

Proof requirements:

- Frozen snapshot exists: at least one experiment is frozen or locked before repeatability review.
- No-lookahead windows: train, validation, test, and paper-forward windows are complete and chronological.
- Version snapshot complete: ranking, reward, forecast, baseline, feature, universe, data-source, code-version, and parameter digest fields are present.
- Out-of-sample result captured: at least one frozen experiment has a pass, weak-pass, or fail verdict.
- Walk-forward pass rate: passed experiments meet the configured v1 pass-rate threshold.
- After-cost support: at least one evaluated experiment includes execution-adjusted reward evidence.

The `proof_summary` includes:

- `status`
- `proof_ready`
- `requirements`
- `record_readiness`
- `summary`
- `safe_next_actions`
- safety notes and mutation flags

Every requirement row includes flags showing it does not change execution, broker routes, risk gates, or ranking weights.

## Walk-Forward Validation Plan

The registry also emits `walk_forward_validation_plan`, a proof-first work queue for turning empty or weak experiment state into repeatability-ready evidence.

The validation plan tracks:

- create and freeze an experiment snapshot
- chronological no-lookahead windows
- complete version snapshots
- out-of-sample result capture
- after-cost support
- walk-forward pass-rate threshold

Each item reports priority, status, linked proof keys, missing evidence fields, blocked claims, a safe next action, and a `done_when` condition. The plan also returns `claim_permissions` so the UI can show that public repeatability, public alpha, institutional readiness, live-trading readiness, and guaranteed-return claims remain blocked.

Validation plan items are internal research gates only. They do not create experiments automatically, fabricate outcomes, authorize orders, change broker routes, bypass risk gates, clear kill switches, alter ranking weights, or approve live trading.

## API Endpoints

Endpoints:

- `GET /api/walk-forward/summary`
- `GET /api/walk-forward/experiments`
- `GET /api/walk-forward/experiments/{experiment_id}`
- `POST /api/walk-forward/experiments`
- `POST /api/walk-forward/experiments/{experiment_id}/freeze`
- `POST /api/walk-forward/experiments/{experiment_id}/clone`

Write endpoints create or update research metadata only:

- Create writes a draft experiment record.
- Freeze changes a draft record to `frozen`.
- Clone copies an existing record into a new draft version.

No endpoint places orders, changes broker routes, changes risk settings, clears kill switches, or changes ranking weights.

## UI Route

Frontend route:

- `/walk-forward`

The page shows:

- experiment list
- status
- walk-forward proof gate
- record readiness
- frozen parameter summary
- date windows
- sample size
- benchmark verdict
- data quality warnings
- baseline-relative edge
- score bucket lift
- forecast accuracy
- execution-adjusted reward
- clone action
- freeze action

The page labels the feature as research metadata and no trading authority.

## Missing Data Dependencies

Walk-forward proof depends on:

- rewardable prediction contracts
- baseline forward returns
- score bucket labels
- forecast validation outputs
- blocker outcome evidence
- AI verdict outcomes
- execution slippage and spread fields
- clean train/validation/test labels

If those fields are missing, experiments can still be created and frozen, but their verdict should remain `insufficient_evidence` or `data_quality_too_weak`.

## Limitations

- V1 is a registry and proof snapshot layer, not a full walk-forward runner.
- It does not fetch external historical data.
- It does not train models.
- It does not tune ranking weights.
- It does not mutate execution, risk, broker, or live-trading settings.
- It stores sanitized metadata only and redacts secret-like fields and local paths.

## Test Commands

From the repository root:

```powershell
python -m compileall -q backend tests scripts
python -m unittest tests.test_walk_forward_experiment_registry tests.test_api_route_health
python -m unittest tests.test_professional_benchmark_suite_service tests.test_data_completeness_audit_service
cd frontend
npm.cmd run build
```

## Candidate Outcome Source

Walk-Forward Experiment Registry v1 evaluates frozen research snapshots against stamped candidate outcomes when available. Outcome records are linked by `candidate_lifecycle_id` and remain append-only research evidence.

Experiment records can use stamped forward returns, baselines, score bucket lift, execution-adjusted reward, and regime labels, but experiment status does not mutate ranking weights, risk settings, broker routes, or trading behavior.
