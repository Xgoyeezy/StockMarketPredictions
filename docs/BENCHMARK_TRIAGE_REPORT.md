# Benchmark Triage Report

Generated: 2026-05-06

Scope: Professional Benchmark Suite v1 inspection only. No trading behavior, broker route, order logic, risk gate, kill switch, AI authority, live-trading setting, or ranking-weight behavior was changed.

## Follow-Up Action Update: 2026-05-08

Status after acting on the category-readiness work order:

- Focused safety and research test set now passes: 138 passed.
- Forecast Validation engine tests were fixed and now pass: 13 passed.
- Live analytics route groups are reachable through the running API.
- Professional Benchmark remains `data_quality_too_weak`.
- Data Completeness remains `needs_attention`.
- Score Calibration remains `insufficient_evidence`.
- Evidence Reward remains `no_rewardable_predictions`.
- Walk-Forward remains `empty`.
- Forecast Validation is `ready`.
- Execution Quality and Portfolio Risk are `ready`.

Current live benchmark/data snapshot:

| Metric | Current value |
| --- | ---: |
| Candidate records in Professional Benchmark | 215 |
| Rewardable Professional Benchmark candidates | 0 |
| Forecast records in Professional Benchmark | 4 |
| Professional Benchmark data quality score | 0% |
| Data Completeness total records | 519 |
| Data Completeness complete records | 1 |
| Data Completeness rewardable records | 1 |
| Data Completeness completion rate | 0.19% |
| Data Completeness rewardability rate | 0.19% |
| Walk-Forward experiments | 0 |

Current highest-priority evidence gaps:

| Field or area | Current count |
| --- | ---: |
| `baseline_forward_return` | 303 |
| `timestamp` | 242 |
| `actual_forward_return` | 180 |
| `order_id_or_trade_id` | 121 |
| `fill_price` | 121 |
| `actual_outcome` | 94 |
| `actual_series` | 4 |
| `paper_trade_outcome` | 2 |

Current safe next actions from Data Completeness:

1. Attach matched benchmark baseline returns at the same timestamp and horizon.
2. Stamp forward returns after each candidate window closes.
3. Attach post-prediction actual path data for forecast validation.

These are manual-review evidence improvements only. They must not change execution, broker routes, risk gates, kill switches, AI authority, or ranking weights automatically.

## Current Verdict

`data_quality_too_weak`

Reason: the benchmark found candidate evidence, AI verdict evidence, blocker labels, score buckets, and forecast validation rows, but candidate rows are not rewardable because more than half of the records are missing required outcome fields.

Current benchmark snapshot:

| Metric | Current value |
| --- | ---: |
| Candidate rows | 158 |
| Rewardable candidate rows | 0 |
| Forecast rows | 4 |
| Validated forecast rows | 3 |
| Data quality score | 0% |
| Average reward | unavailable |
| Expected value | unavailable |
| Score bucket lift | unavailable |
| Baseline-relative edge | unavailable |
| Slippage-adjusted reward | unavailable |

The suite is functioning correctly by refusing to claim edge without rewardable candidate contracts and explicit baselines.

## Tests Run

Backend and API validation:

```powershell
python -m compileall -q backend tests scripts
python -m unittest tests.test_professional_benchmark_suite_service tests.test_evidence_reward_engine_service tests.test_forecast_validation_engine tests.test_forecast_validation_api tests.test_api_route_health tests.test_trade_automation_candidate_diagnostics tests.test_execution_router tests.test_automation_exit_execution_watchdog_service
```

Result: passed, 63 tests.

Frontend validation:

```powershell
cd frontend
npm.cmd run build
```

Result: passed. The production build includes the Professional Benchmark page.

Observed warning: a unittest ResourceWarning reported one unclosed SQLite connection from the existing app test harness. It did not fail the suite and was not caused by benchmark calculations.

## Test Failures

None.

## Available Data

Strongest available data:

- Candidate evidence rows exist.
- Score bucket labels exist across `90_100`, `80_89`, `60_79`, `40_59`, and `0_39`.
- AI verdict labels exist for `wait_for_confirmation`, `reject_evidence`, and `approve_evidence`.
- Blocker labels exist and can be grouped.
- Forecast Validation is usable: 4 forecast records, 3 rewardable validated forecasts.
- Forecast metrics are available: direction accuracy, path MAE, path RMSE, timing error, confidence calibration, and forecast reward.

Current strongest benchmark section:

| Section | Status | Notes |
| --- | --- | --- |
| Forecast accuracy | available | 3 validated forecasts, direction accuracy currently 66.67%. |
| AI verdict accuracy | partially available | 94 verdict labels exist, but no rewardable candidate outcomes yet. |
| Blocker value | partially available | Blockers are grouped, but blocker outcome quality depends on forward returns. |
| Reward by setup/engine/regime | structurally available | Grouping exists, but averages are unavailable because rewardable rows are zero. |
| Data quality | available | Correctly identifies missing required fields. |

## Missing Or Weak Data

Required benchmark fields currently missing or weak:

| Field or area | Current issue |
| --- | --- |
| `actual_forward_return` | Missing on 97 candidate rows. |
| `baseline_forward_return` | Missing on all candidate rows. |
| `forecast_series` | Present in Forecast Validation fixtures, not yet broadly tied to candidate rows. |
| `actual_series` | Present for most forecast rows; one forecast row has incomplete actual data. |
| Spread at signal | Missing from rewardable benchmark rows. |
| Slippage | Missing from rewardable benchmark rows. |
| Engine labels | Present enough for grouping, but not useful without rewardable outcomes. |
| Setup labels | Present enough for grouping, but not useful without rewardable outcomes. |
| Regime labels | Missing on all candidate rows in the benchmark normalization. |
| Blocker outcomes | Blocker labels exist; blocker outcome value is weak because forward returns and total rewards are missing. |
| AI verdict outcomes | AI verdict labels exist; outcome accuracy is weak because rewardable candidate outcomes are missing. |
| Paper trade outcome data | Not available as rewardable candidate outcomes in the current benchmark rows. |
| Execution cost data | Missing `slippage_bps` and `spread_bps`, so execution-adjusted reward is unavailable. |
| Score bucket data | Buckets exist, but `total_reward` is missing, so score bucket lift is unavailable. |
| Out-of-sample period data | Missing `sample_split` and `experiment_version`. |

Additional required prediction contract gaps:

- `prediction_horizon_minutes` missing on all candidate rows.
- `predicted_target_pct` missing on all candidate rows.
- `total_reward` missing on all candidate rows.
- `predicted_direction` missing on many candidate rows.
- `prediction_created_at` missing on many candidate rows.
- `confidence` missing on many candidate rows.
- `invalidation_level` missing on many candidate rows.

## Weakest Benchmark Sections

| Section | Why it is weak |
| --- | --- |
| Baseline comparison | No explicit SPY, QQQ, sector ETF, random candidate, momentum, mean reversion, VWAP reclaim, or opening range baseline forward returns are present. |
| Score bucket separation | Score buckets exist, but total reward is missing, so bucket lift cannot be computed. |
| Execution quality | Slippage and spread fields are missing from rewardable candidate rows. |
| Slippage-adjusted reward | Blocked by missing candidate reward and execution cost fields. |
| Missed move recovery | Missed-move outcome fields are not available in the benchmark report. |
| Out-of-sample stability | Missing experiment versions and sample-split labels. |

## Strongest Benchmark Sections

| Section | Why it is strongest |
| --- | --- |
| Forecast accuracy | It already has rewardable forecast contracts, actual path evidence, path error, timing, confidence calibration, and forecast reward. |
| AI verdict inventory | Verdict labels are present across 94 rows, so once outcomes are attached, AI verdict accuracy can become useful quickly. |
| Score bucket inventory | Buckets are present across candidate rows, so the score-separation report is structurally ready once rewards are attached. |
| Blocker inventory | Blocker labels are present, so blocker value can become useful once blocked candidates receive forward outcomes and baselines. |

## Highest Priority Data Fixes

1. Add candidate forward outcome stamping.

   Every candidate lifecycle row needs post-scan forward outcomes: 5-minute, 15-minute, 30-minute, and selected horizon return. This is the first blocker because reward, blocker value, AI verdict accuracy, and score bucket lift all depend on actual forward returns.

2. Add explicit baseline returns at the same timestamp and horizon.

   Store SPY, QQQ, sector ETF, random candidate, simple momentum, simple mean reversion, VWAP reclaim, and opening range breakout baseline forward returns. Without this, the suite cannot answer whether the system beat simple baselines after costs.

3. Emit complete pre-move prediction contracts for candidates.

   Candidate rows need `prediction_created_at`, `predicted_direction`, `prediction_horizon_minutes`, `predicted_target_pct`, `invalidation_level`, and `confidence` before the move is measured. Vague setup labels must stay non-rewardable.

4. Add execution-cost snapshots at signal time.

   Store spread at signal, expected slippage, realized slippage when traded, and route readiness. This unlocks slippage-adjusted reward and execution-quality proof.

5. Add regime and experiment labels.

   Store timestamped regime labels, `experiment_version`, `reward_formula_version`, and `sample_split`. This unlocks out-of-sample stability and walk-forward proof.

## Recommended Next Build

Build a Candidate Outcome And Baseline Stamping pass.

Minimum deliverables:

- For every scanned candidate, append forward returns at 5, 15, 30, and horizon-specific windows.
- Attach matched baselines for SPY, QQQ, sector ETF, random candidate, simple momentum, simple mean reversion, simple VWAP reclaim, and opening range breakout.
- Stamp candidate rows with spread, slippage estimate, regime, experiment version, and reward formula version.
- Keep this as evidence/reporting only.
- Do not change ranking weights automatically.
- Do not change order routing.
- Do not submit trades from benchmark results.

Why this is the next build: the current bottleneck is not another dashboard, model, strategy, or AI layer. The benchmark needs rewardable candidate rows with forward-known outcomes and baselines. Once that exists, the current suite can begin answering whether Quant Evidence OS beats baselines after costs.

## What Not To Build Yet

Do not build these before candidate outcome and baseline stamping:

- More strategies.
- More AI reviewers.
- A larger benchmark UI.
- More simulation volume counted as live-observed proof.
- Automatic ranking-weight updates.
- Live trading expansion.
- True HFT execution claims.
- Portfolio optimization based on current benchmark outputs.

Those would add complexity before the system can prove that candidate scores, blockers, AI verdicts, and setups have measurable edge.

## Safety Boundaries Preserved

- No trading behavior changed.
- No broker route changed.
- No order logic changed.
- No risk gate changed.
- No kill switch behavior changed.
- No AI order authority added.
- No benchmark result can submit orders.
- No benchmark result can change ranking weights automatically.
- No simulation evidence was merged into live-observed evidence.
- No secrets, broker records, raw logs, or raw local paths are included in this report.

## Bottom Line

The Professional Benchmark Suite v1 is working as intended, but it is blocked by data quality. The next bottleneck is candidate-level outcome attribution, not benchmark code. Build the candidate outcome and baseline stamping layer next, then rerun the benchmark after enough rewardable rows accumulate.

## Project Finish Tracker

This tracker is project-wide and must stay at the end of report outputs. It is not limited to the Professional Benchmark layer.

Summary: 26 tracked items; 5 critical open items; 1 done; 11 in progress; 6 blocked by evidence; 1 not started; 7 deferred.

Proof-first rule: Ambition is allowed. Proof decides priority.

| Priority | Area | Item | Status | Done when |
| --- | --- | --- | --- | --- |
| Critical | Verification | Post-Implementation Verification | Done | The verification report is current, cites focused test/build/browser evidence, and lists remaining proof blockers without overclaiming readiness. |
| Critical | Evidence Quality | Data completeness hardening | In Progress | Data Completeness reports benchmark_ready and proof_field_ready with traceable source coverage. |
| Critical | Evidence Capture | Candidate outcome and baseline stamping | In Progress | Rewardable candidate outcomes exist with actual_forward_return, baseline_forward_return, cost fields, and append-only lineage. |
| Critical | Benchmarking | Professional Benchmark proof gate | Blocked By Evidence | Professional Benchmark reaches ready_for_human_review without claiming proven alpha. |
| High | Repeatability | Walk-forward validation | Blocked By Evidence | Walk-Forward shows frozen, no-lookahead, evaluated records with acceptable pass rate. |
| High | Ranking Quality | Score calibration and feature attribution | Blocked By Evidence | Calibration proof is ready with sufficient feature coverage and after-cost lift. |
| High | Execution Quality | Execution Quality and TCA | In Progress | Execution proof is ready with candidate-route linkage and positive after-cost evidence. |
| Critical | Risk And Audit | Risk Gate and Audit Trail hardening | In Progress | Risk and audit evidence is traceable, sanitized, and confirms no proof layer can bypass controls. |
| High | Risk Visibility | Portfolio Risk Intelligence | In Progress | Portfolio risk proof is ready with enough exposure and context coverage for review. |
| Medium | Decision Review | Human vs System Shadow Mode | Blocked By Evidence | Shadow Mode has same-opportunity comparisons with pre-outcome human and system contracts. |
| High | Promotion Governance | Research promotion rules | Blocked By Evidence | Promotion proof is ready with traceability coverage and no authority crossing. |
| High | Reward Quality | Evidence Reward and blocker value | Blocked By Evidence | Evidence Reward can explain rewardability, blocker value, and after-cost outcomes without fabricated data or ranking mutation. |
| Medium | Forecast Quality | Forecast validation hardening | In Progress | Forecast Validation stays ready with broad actual-path coverage and stable reward calculations. |
| Medium | Proof Visibility | Proof metrics dashboard planning | In Progress | A shared proof-metrics view shows the current proof gaps and which gate each gap blocks. |
| High | Roadmap Discipline | Proof-first backlog scoring and expansion gates | In Progress | Every future feature has a proof-first decision of near-term, foundation-first, future backlog, or reject for now. |
| High | Setup Research | Technical Analysis evidence setup admission | In Progress | Technical-analysis methods are classified into evidence-only, research-only, and avoid groups with method-specific fields, controls, and proof gates documented before implementation. |
| Medium | Ai Research | AI Committee research layer | In Progress | Committee reports add research context without approving trades or mutating live behavior. |
| Medium | Product Readiness | Operator experience, docs, and report UX | In Progress | Every major report ends with the shared finish tracker and clear next safe actions. |
| Critical | Live Trading Boundary | Paper-to-live proof gate | Not Started | Live enablement remains explicitly gated by verified paper evidence and human approval. |
| Future | Future Backlog | Market Specialist Desk registry | Deferred | Deferred until foundation proof is stronger and the smallest safe context-only version is justified. |
| Future | Future Backlog | Candidate Fusion and Market x Strategy Benchmark | Deferred | Deferred until current benchmark, walk-forward, and candidate evidence can support market x strategy comparisons. |
| Future | Future Backlog | Off-Exchange Liquidity Dashboard | Deferred | Deferred until it solves a measured proof problem without changing ranking, routing, or order behavior. |
| Future | Future Backlog | Broker-neutral architecture and provider ROI gates | Deferred | Deferred until data, benchmark, execution, or walk-forward evidence proves a broker/provider bottleneck and ROI case. |
| Future | Future Backlog | Visual Strategy Evidence Builder | Deferred | Deferred until current evidence contracts are mature enough to make a visual builder proof-focused instead of feature-count-focused. |
| Future | Future Backlog | Governance, RBAC, model registry, and institutional controls | Deferred | Deferred until the proof chain supports firm-facing control work and the required reviews are scoped. |
| Future | Future Backlog | C++ Core Accelerators and HFT feasibility study | Deferred | Deferred until profiling proves a research-only acceleration bottleneck or a separate HFT thesis is approved. |

Safety boundary: tracker items are verification, proof, review, documentation, paper-operation, or deferred roadmap work only. They do not authorize live trading or expansion implementation. They do not authorize order submission, broker-route changes, risk-gate changes, kill-switch changes, automatic ranking-weight changes, or deferred expansion work without separate proof-first approval.
