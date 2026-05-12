# Proof Metrics Dashboard

This document describes the read-only Proof Metrics Dashboard implementation for Quant Evidence OS. The dashboard exists to show proof gaps and the gate each gap blocks. It is not an edge claim, readiness claim, live-trading approval, broker-route change, risk-gate change, kill-switch change, or ranking-weight change.

## Scope

The dashboard aggregates source report summaries from:

- Data Completeness
- Evidence Outcomes
- Professional Benchmark
- Walk-Forward
- Score Calibration
- Evidence Reward
- Execution Quality
- Risk and Audit
- Portfolio Risk
- Forecast Validation
- Human vs System Shadow Mode
- Research Promotion
- AI Committee

Every metric is manual-review only. The dashboard can say a proof gate is blocked or ready for human review, but it cannot submit orders, change broker routes, bypass risk gates, clear kill switches, change ranking weights, or implement deferred expansion work.

## Implemented Surfaces

Backend:

- `backend/services/proof_metrics_dashboard.py`
- `backend/routers/proof_metrics.py`
- `GET /api/proof-metrics/summary`

Frontend:

- `frontend/src/pages/ProofMetricsPage.jsx`
- `getProofMetricsSummary()` in `frontend/src/api/client.js`
- Navigation route `/proof-metrics`

The page ends with the shared Project Finish Tracker, consistent with the report-footer requirement.

## Metric Gates

| Metric | Gate | Blocks |
| --- | --- | --- |
| Proof-field coverage | Data Gate | Benchmark readiness, walk-forward readiness, paper-to-live review |
| Candidate outcome and baseline coverage | Evidence Outcome Gate | Baseline-relative edge, rewardability, score calibration |
| Execution-cost coverage | Execution Quality Gate | After-cost edge, tradability review, paper-to-live review |
| Professional benchmark proof | Benchmark Gate | Proven-alpha language, repeatability language, public edge claims |
| Frozen walk-forward proof | Walk-Forward Gate | Repeatability claims, promotion review, paper-to-live review |
| Score calibration proof | Score Calibration Gate | Score quality claims, automatic ranking changes, promotion review |
| Evidence rewardability | Reward Gate | Reward claims, blocker value claims, benchmark input quality |
| Execution quality proof | Execution Quality Gate | Tradability claims, route-quality claims, paper-to-live review |
| Risk and audit hardening | Safety Gate | Risk-gate authority claims, kill-switch clearance, live-trading readiness |
| Portfolio risk proof | Portfolio Risk Gate | Portfolio-readiness claims, paper-to-live review |
| Forecast validation coverage | Forecast Validation Gate | Forecast-accuracy claims, benchmark forecast support |
| Same-opportunity shadow comparisons | Review Gate | Human-vs-system quality claims, override-quality claims |
| Research promotion traceability | Promotion Gate | Policy promotion, ranking mutation, paper-to-live review |
| AI committee safety boundary | AI Review Gate | AI order authority, AI risk-gate override, AI ranking mutation |

## Acceptance Criteria

1. The API returns `research_only: true`, `read_only: true`, `proof_visibility_only: true`, `can_submit_orders: false`, `can_submit_live_orders: false`, and `mutation: "none"`.
2. Source report failures degrade to `source_unavailable` and do not crash the dashboard.
3. Gate groups show ready/open metric counts, top gap, blocked claims, and safe next actions.
4. The frontend route shows safety boundaries, source report health, gate groups, proof actions, deferred scope, warnings, and the Project Finish Tracker.
5. Tests cover service behavior, API shape, frontend route wiring, and absence of execution/broker/risk/ranking mutation calls.

## Current Status

Status: In Progress

The first implementation is in place as a proof visibility surface. It still reports proof gaps as blocked until the underlying source reports contain enough outcome, baseline, execution, walk-forward, calibration, risk, and review evidence.

Safety boundary: Proof Metrics is reporting only. It does not authorize live trading, order submission, broker-route changes, risk-gate changes, kill-switch changes, automatic ranking-weight changes, or deferred expansion work.

## Project Finish Tracker

This tracker is project-wide and must stay at the end of report outputs. It is not limited to the current proof layer.

Summary: 26 tracked items; 6 critical open items; 12 in progress; 6 blocked by evidence; 1 not started; 7 deferred.

Proof-first rule: Ambition is allowed. Proof decides priority.

| Priority | Area | Item | Status | Done when |
| --- | --- | --- | --- | --- |
| Critical | Verification | Post-Implementation Verification | In Progress | The verification report is current, cites focused test/build/browser evidence, and lists remaining proof blockers without overclaiming readiness. |
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
