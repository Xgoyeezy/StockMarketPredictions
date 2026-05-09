# Research Promotion Rules

## Purpose

Research Promotion Rules v1 assigns research statuses to strategies, setup types, engines, blockers, forecast models, AI verdict policies, ranking rules, and risk rules. It is a proof and governance layer for human review. It does not promote anything to live trading, does not change paper execution, and does not modify ranking or risk settings.

## Entity Types

- `strategy`
- `setup_type`
- `engine`
- `blocker`
- `forecast_model`
- `AI_verdict_policy`
- `ranking_rule`
- `risk_rule`

## Status Definitions

- `research`: Evidence is being collected or reviewed.
- `candidate`: Evidence is promising enough for human research review.
- `walk_forward_testing`: A frozen walk-forward experiment exists and benchmark-ready evidence is available.
- `paper_proven`: Paper research evidence passed v1 checks. This is not live approval.
- `rejected`: Evidence shows negative edge, severe data-quality failure, poor forecast accuracy, poor execution-adjusted reward, or severe blocker/AI failure.
- `needs_more_evidence`: Required samples or fields are missing.

## Promotion Criteria

Candidate status requires:

- Minimum sample size.
- Minimum rewardable count.
- Minimum data completeness and rewardability.
- Positive or neutral benchmark result.
- No severe safety or data-quality warnings.

Walk-forward testing requires:

- A frozen experiment record.
- Required data fields present.
- Baseline comparison available.
- Rewardability threshold met.

Paper-proven research status requires:

- Positive baseline-relative reward.
- Acceptable drawdown.
- Acceptable execution-adjusted reward.
- Stable or positive score-bucket separation when available.
- No severe data-quality warning.
- Walk-forward pass or weak pass.

Rejected status can be assigned when there is enough sample and one or more severe failures:

- Negative baseline-relative reward.
- Poor forecast accuracy.
- High AI false-positive or false-negative rate.
- Poor execution-adjusted reward.
- Severe blocker false-block rate or negative blocker value.
- Severe data-quality failure.

Needs-more-evidence status applies when:

- Sample size is too small.
- Rewardable count is too small.
- Forward returns, baseline returns, execution data, or regime labels are missing.

## What Paper-Proven Means

`paper_proven` means the entity has passed a research-only paper evidence threshold. It may justify a human review of whether the research hypothesis deserves more controlled testing.

## What Paper-Proven Does Not Mean

`paper_proven` does not mean:

- Live trading is approved.
- Broker routes change.
- Risk gates can be bypassed.
- Ranking weights change automatically.
- AI can place or approve orders.
- Position size, limits, or kill-switch behavior changes.

## API Endpoints

All endpoints are under the configured API prefix, usually `/api`.

- `GET /api/research-promotion/summary`
- `GET /api/research-promotion/entities`
- `GET /api/research-promotion/entities/{entity_id}`
- `POST /api/research-promotion/entities/{entity_id}/status`

The POST endpoint writes sanitized research metadata only. It does not mutate strategy, broker, execution, risk, live-trading, or ranking configuration.

## UI Route

- `/research-promotion`

The page shows the entity list, current research status, sample size, benchmark verdict, walk-forward status, data-quality warnings, criteria passed, criteria failed, and manual research metadata status controls.

## Role Model

Research Promotion uses a small-fund workflow role model for metadata review only:

- `operator`: can view research states and add operational notes.
- `researcher`: can view, comment, and propose research metadata status changes.
- `risk_manager`: can view, comment, hold, or reject promotion metadata when risk evidence is incomplete or blocking.
- `admin`: can view, comment, approve, reject, hold, rollback, or propose research metadata status changes.

These roles do not grant broker authority, order authority, live-money authority, risk-gate bypass authority, kill-switch bypass authority, or automatic ranking-weight authority.

## Strategy Promotion Process

The strategy promotion process is a review workflow, not a deployment workflow:

1. A strategy, setup, engine, blocker, forecast model, AI verdict policy, ranking rule, or risk rule appears as a research entity.
2. The reviewer inspects benchmark, walk-forward, data completeness, execution quality, portfolio risk, and evidence snapshot references.
3. A permitted role records `research`, `candidate`, `walk_forward_testing`, `paper_proven`, `rejected`, or `needs_more_evidence` as sanitized research metadata.
4. Approval records must preserve who changed what, when it changed, the previous status, the new status, the reason, and the linked evidence snapshot.
5. A rollback is also a metadata event and must not alter execution configuration, broker routes, risk gates, ranking weights, or live-trading state.

## Incident And Release Workflow

Incident and release records are required before small-fund readiness can be treated as firm-style workflow evidence:

- Incidents must identify the affected research entity, evidence snapshot, reviewer, timestamp, status, and corrective action.
- Release validation must identify the release candidate, validation checks, reviewer, timestamp, result, and rollback note.
- Audit records should be append-only and tamper-evident through event identifiers and hash links.
- Incident closure and release approval remain research and operations metadata only.
- Incident records must not auto-clear kill switches, bypass risk gates, change broker routes, change order settings, or enable live-money autonomy.

## Safety Boundary

Research Promotion v1 always reports:

- `research_only: true`
- `can_submit_orders: false`
- `can_submit_live_orders: false`
- `writes_execution_config: false`
- `writes_broker_config: false`
- `writes_risk_config: false`
- `writes_ranking_config: false`

The layer cannot place orders, change broker routes, bypass risk gates, clear kill switches, enable live trading, or grant AI order authority.

## Metadata Storage

Manual research statuses are stored as sanitized metadata under:

- `runtime-exports/research-promotion/promotion_statuses.json`

Secret-like keys and raw local paths are redacted before storage/output. This store is not an execution config.

## Test Commands

```powershell
python -m compileall -q backend tests scripts
python -m unittest tests.test_research_promotion_rules tests.test_api_route_health
python -m unittest tests.test_professional_benchmark_suite_service tests.test_data_completeness_audit_service tests.test_walk_forward_experiment_registry tests.test_research_promotion_rules tests.test_api_route_health
npm.cmd run build
```

Run the frontend build from:

```powershell
cd frontend
```

## Limitations

- v1 criteria are transparent thresholds, not statistical proof of live alpha.
- Missing forward returns, baselines, execution costs, or regime labels can force `needs_more_evidence`.
- Manual status changes are metadata only and should be treated as review notes, not deployment decisions.
- The layer depends on Professional Benchmark Suite, Data Completeness, and Walk-Forward Experiment Registry outputs.

## Candidate Outcome Source

Research Promotion Rules consume stamped candidate outcomes through the benchmark, data completeness, and walk-forward layers. Candidate outcomes are append-only research records linked by `candidate_lifecycle_id`.

`paper_proven` remains a research status, not live approval. Outcome evidence can inform manual review, but promotion status must not enable live trading, change ranking weights, change risk limits, change broker routes, bypass risk gates, or trigger orders.
