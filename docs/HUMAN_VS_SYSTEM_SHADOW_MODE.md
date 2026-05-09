# Human vs System Shadow Mode v1

## Purpose

Human vs System Shadow Mode measures whether Quant Evidence OS improves or beats human trader judgment on the same opportunity set. It captures human forecast contracts and compares them against system forecast or candidate contracts when outcome evidence is available.

This is research metadata and analytics only.

## Methodology

Human vs System Shadow Mode compares human thesis records and system records on the same opportunity set. The preferred match key is `linked_candidate_id`; symbol fallback is allowed only when candidate linkage is unavailable. A complete comparison requires human direction, confidence, target, invalidation, horizon, timestamp, system direction, system confidence, system target, system invalidation, system horizon, outcome fields, and cost assumptions.

Human decisions must be recorded before the outcome window closes. Closed outcome records should be treated as immutable research evidence, with review notes stored separately from any execution authority.

Methodology outputs are readiness evidence only. They are not proof that the system beats skilled human traders, not investor performance claims, and not live-trading approval.

## Safety Boundary

Shadow Mode does not:

- place trades
- create broker orders
- change broker routes
- bypass risk gates
- clear kill switches
- change risk limits
- change ranking weights automatically
- grant AI order authority
- enable live trading

The service responses include:

- `research_only: true`
- `can_submit_orders: false`
- `can_submit_live_orders: false`
- `mutation: "research_metadata_only"`

The only allowed write is a sanitized human thesis research metadata record.

## Human Forecast Contract

A human thesis record may include:

- `human_thesis_id`
- `created_at`
- `symbol`
- `linked_candidate_id`
- `human_direction`
- `human_confidence`
- `human_target_pct`
- `human_invalidation_level`
- `human_horizon_minutes`
- `human_reason`
- `setup_type`
- `engine`
- `regime`

Rewardability requires:

- `symbol`
- `human_direction`
- `human_confidence`
- `human_target_pct`
- `human_invalidation_level`
- `human_horizon_minutes`
- `actual_forward_return`
- `baseline_forward_return`

Vague labels such as “bullish chart” are not rewardable. The thesis must be timestamped and specific about direction, horizon, target, invalidation, and confidence.

## System Comparison Contract

The system side may come from Forecast Validation, Evidence Reward, or explicit fields on the human thesis record:

- `system_prediction_id`
- `system_direction`
- `system_confidence`
- `system_target_pct`
- `system_invalidation_level`
- `system_horizon_minutes`
- `system_forecast_reward`
- `system_candidate_reward`

The service matches system evidence by `linked_candidate_id` first, then by symbol when candidate linkage is unavailable.

## Outcome Fields

Outcome scoring uses:

- `actual_forward_return`
- `baseline_forward_return`
- `target_hit`
- `invalidation_hit`
- `max_adverse_excursion`
- `time_to_target`

Missing outcomes do not crash the service. They are reported in `missing_fields`, and the row remains visible as incomplete research evidence.

## Reward Formula

For each rewardable side:

```text
total_reward =
direction_score
+ target_score
+ baseline_relative_score
- adverse_penalty
- invalidation_penalty
- late_penalty
- confidence_penalty
```

Where:

- `direction_score` is `+1` for correct direction and `-1` for wrong direction.
- `target_score` is `+0.5` when target is hit and `-0.25` when missed.
- `baseline_relative_score` is actual forward return minus baseline forward return.
- `adverse_penalty` is `abs(max_adverse_excursion) * 0.5`.
- `invalidation_penalty` is `0.75` when invalidation is hit.
- `late_penalty` is `0.25` when target is hit after the stated horizon.
- `confidence_penalty` is confidence calibration error times `0.5`.

The formula is simple and transparent in v1. It is not a trading model.

## Analytics

The summary computes:

- human direction accuracy
- system direction accuracy
- human target hit rate
- system target hit rate
- human invalidation hit rate
- system invalidation hit rate
- human average reward
- system average reward
- human vs system edge
- human false positive rate
- system false positive rate
- human false negative rate
- system false negative rate
- override quality
- missed winner comparison
- bias diagnostics

## Override Quality Definitions

An override is a same-opportunity record where the human direction differs from the system direction. Override quality is evaluated after costs and risk context, using:

- human reward after spread, slippage, and fill assumptions
- system reward after spread, slippage, and fill assumptions
- blocker state at decision time
- risk gate state at decision time
- kill-switch state at decision time
- portfolio exposure or other available risk context

A good override is one where the human decision improves net decision quality after costs and risk adjustment. A poor override is one where the human decision underperforms the system, ignores useful blockers, conflicts with risk context, or adds avoidable false positives or false negatives.

Override quality is a research score only. It cannot submit orders, route orders, change broker routes, bypass risk gates, clear kill switches, change ranking weights, or enable live-money autonomy.

## Bias Diagnostics

V1 flags:

- chasing extended moves
- high confidence wrong calls
- late entries
- holding invalidated ideas
- ignoring good blockers
- overriding strong system evidence
- underconfidence on good calls

Bias diagnostics are review prompts only. They do not update strategy, ranking, risk, or broker configuration.

## API Endpoints

- `GET /api/shadow-mode/summary`
- `GET /api/shadow-mode/records`
- `GET /api/shadow-mode/comparisons`
- `GET /api/shadow-mode/bias`
- `POST /api/shadow-mode/human-thesis`

`POST /human-thesis` writes research metadata only.

## UI Route

Open:

- `/shadow-mode`

The page shows the research-only boundary, a human thesis form, human-vs-system summary, comparison rows, direction accuracy, reward comparison, target/invalidation comparisons, missed-winner comparison, bias diagnostics, and missing data warnings.

## Test Commands

```powershell
python -m unittest tests.test_human_system_shadow_mode tests.test_api_route_health
python -m compileall -q backend tests scripts
npm.cmd run build
```

Run the frontend build from `frontend/`.

## Limitations

- V1 relies on available local forecast/reward evidence and explicit outcome fields.
- It does not fabricate forward returns or baselines.
- System matching is simple: linked candidate first, symbol fallback second.
- Bias diagnostics are heuristic review labels, not formal psychology or execution advice.
- Shadow results do not trigger trades or automatic ranking changes.

## Candidate Outcome Source

Shadow Mode can compare human thesis records against stamped system candidate outcomes when `candidate_lifecycle_id` links exist. Outcome stamps provide the system-side forward return, baseline return, target hit, invalidation hit, and reward fields.

Human thesis and shadow comparison records remain research metadata only. They do not create orders, change execution, change broker routes, change risk gates, or automatically change ranking weights.
