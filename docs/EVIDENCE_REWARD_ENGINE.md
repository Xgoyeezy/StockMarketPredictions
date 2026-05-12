# Evidence Reward Engine

## Purpose

Evidence Reward scores timestamped candidate prediction contracts against forward outcomes, matched baselines, blocker context, and paper-route execution cost evidence.

It is research-only. Rewards do not place trades, clear gates, change broker routes, change risk settings, or automatically change ranking weights.

## Candidate Outcome Source

Evidence Reward consumes:

- original lifecycle rows from `runtime-exports/candidate-lifecycle/<date>/<tenant>.jsonl`
- append-only outcome rows from `runtime-exports/candidate-outcomes/<date>/<tenant>.jsonl`

The rows are merged by `candidate_lifecycle_id` at report time. The original lifecycle row is not edited.

## Required Reward Fields

Rewardable candidate contracts require:

- `prediction_created_at`
- `predicted_direction`
- `prediction_horizon_minutes`
- `predicted_target_pct`
- `invalidation_level`
- `confidence`
- `actual_forward_return`
- `baseline_forward_return`

If these fields are missing, the row stays visible but is not rewardable.

## Output Consumers

Evidence Reward is the shared source for:

- Professional Benchmark Suite
- Data Completeness Layer
- Score Calibration and Feature Attribution
- Execution Quality and TCA
- Walk-Forward Experiment Registry
- Research Promotion Rules
- Human vs System Shadow Mode
- Portfolio Risk Intelligence

## Missing Data Behavior

Missing forward returns, baselines, execution costs, score fields, or regime labels are reported as missing fields. They are not inferred from current price and are not fabricated.

Simulation evidence remains separate from real-time market-observed evidence.

## Evidence Reward Cleanup Plan

The report includes `evidence_reward_cleanup_plan` and `aggregations.evidence_reward_cleanup_plan`.

This is the proof-first cleanup layer for Evidence Reward and blocker value. It turns rewardability gaps into explicit manual cleanup items so incomplete evidence cannot be mistaken for alpha, tradability, ranking approval, paper-to-live readiness, or live-trading readiness.

Cleanup items:

- candidate evidence sample
- rewardable prediction contracts
- outcome and baseline coverage
- execution cost context
- blocker value evidence
- simulation evidence separation
- manual ranking review boundary

The summary exposes:

- `reward_cleanup_status`
- `reward_cleanup_open_items`
- `reward_cleanup_critical_open_items`
- `top_cleanup_item`
- `claim_permissions`

Claim permissions remain conservative:

- `cautious_internal_reward_review` may become true only when rewardable prediction contracts exist.
- `blocker_value_review` may become true only when blocker value has rewardable blocked rows.
- `after_cost_reward_review` may become true only when execution-cost context exists.
- `public_alpha_claim` remains false.
- `automatic_ranking_mutation` remains false.
- `paper_to_live_readiness` remains false.
- `live_trading_readiness` remains false.

Blocked claims include proven alpha, reward-quality claims, blocker-value claims, after-cost reward claims, automatic ranking mutation, paper-to-live readiness, and live-trading readiness.

The cleanup plan is research metadata only. It does not submit orders, trigger paper orders, enable live trading, change broker routes, bypass risk gates, clear kill switches, grant AI order authority, merge simulation evidence into real-time market-observed evidence, or mutate ranking weights.

## Safety Boundary

Evidence Reward may recommend manual research review. It must not:

- submit orders
- enable live trading
- change broker routes
- bypass risk gates
- clear kill switches
- grant AI order authority
- automatically change ranking weights

## Test Commands

```powershell
python -m pytest tests\test_evidence_reward_engine_service.py
python -m pytest tests\test_candidate_outcome_stamping_service.py tests\test_evidence_reward_engine_service.py -q
```
