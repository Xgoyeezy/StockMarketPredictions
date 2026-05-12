# Ten Out Of Ten Category Upgrade Master Plan

Quant Evidence OS, implemented as `StockMarketPredictions`, is a Python/FastAPI plus React/Vite trading evidence and research operating system. It scans equities and ETFs, runs paper-first strategy desks, ranks paper trade candidates, supervises hard risk gates, explains trade and no-trade decisions, tracks blockers, missed opportunities, AI reviews, paper orders, fills, reconciliation state, watchdog state, forecast validation, evidence rewards, execution quality, benchmark research, walk-forward research, and reusable decision evidence.

This is a planning, roadmap, architecture, acceptance criteria, and backlog document only. It does not implement backend services, add API routes, add frontend pages, modify execution code, modify broker code, modify live-control code, modify risk gate code, enable live trading, or change trading behavior.

## Canonical References

- Category ratings: `docs/CATEGORY_READINESS_RATINGS.md`
- Current state and professional ratings: `docs/CURRENT_STATE_AND_PROFESSIONAL_RATINGS.md`
- Buyer positioning: `docs/PRODUCT_POSITIONING_AND_BUYER_CATEGORIES.md`
- Existing 10/10 roadmap: `docs/TEN_OUT_OF_TEN_ROADMAP.md`
- Proof-first roadmap discipline: `docs/PROOF_FIRST_ROADMAP_DISCIPLINE.md`
- Future expansion backlog: `docs/TEN_OUT_OF_TEN_ROADMAP.md#future-expansion-backlog`
- Acceptance checklist: `docs/TEN_OUT_OF_TEN_ACCEPTANCE_CHECKLIST.md`
- 30-60-90 day plan: `docs/TEN_OUT_OF_TEN_30_60_90_DAY_PLAN.md`
- Proof gates: `docs/TEN_OUT_OF_TEN_PROOF_GATES.md`
- Technical Analysis evidence setup research: `docs/TECHNICAL_ANALYSIS_EVIDENCE_SETUP_RESEARCH.md`
- Professional Benchmark: `docs/PROFESSIONAL_BENCHMARK_SUITE.md`
- Walk-Forward: `docs/WALK_FORWARD_EXPERIMENT_REGISTRY.md`
- Data Completeness: `docs/DATA_COMPLETENESS_LAYER.md`
- Evidence Reward: `docs/EVIDENCE_REWARD_ENGINE.md`
- Forecast Validation: `docs/quant_evidence_os_summary.md`
- Execution Quality: `docs/EXECUTION_QUALITY_TCA.md`
- Research Promotion: `docs/RESEARCH_PROMOTION_RULES.md`
- Portfolio Risk: `docs/PORTFOLIO_RISK_INTELLIGENCE.md`
- Human vs System: `docs/HUMAN_VS_SYSTEM_SHADOW_MODE.md`
- Hedge Fund AI Agents: `docs/HEDGE_FUND_AI_AGENTS.md`
- Safety: `docs/trading_safety_hardening.md`

## Current Ratings

These are current estimated readiness scores. They are not official industry ratings, not proof of alpha, and not investor performance claims.

| Rank | Category | Current estimated readiness | 10/10 target |
| ---: | --- | ---: | --- |
| 1 | Retail trading bot | 9/10 | A non-technical paper trader can run, understand, review, and export proof without touching code. |
| 2 | Solo systematic trader platform | 7.5/10 | Higher-ranked candidates outperform lower-ranked candidates after costs across frozen out-of-sample tests and multiple regimes. |
| 3 | Small prop shop or small fund research stack | 6/10 | A team can review, approve, reject, promote, roll back, and audit strategy work with active risk controls proven. |
| 4 | Top discretionary trader comparison | 5/10 | The system improves or beats skilled human decision quality on the same opportunity set after costs and risk adjustment. |
| 5 | Institutional quant desk or enterprise control plane | 3/10 | An evaluator can inspect lineage, controls, approvals, evidence, forecasts, rewards, risks, incidents, and permissions without verbal explanation. |
| 6 | HFT or elite execution platform | 2/10 | Only if a separate future infrastructure thesis proves latency-sensitive execution against professional market infrastructure. |

## Future Potential Ratings With Roadmap Additions

These are future potential estimates, not current implementation ratings. They are not proof of alpha, not investor performance claims, and not a reason to weaken proof gates. They assume Market Specialist Desks, Visual Strategy Evidence Builder, Hedge Fund AI Role Agents, Off-Exchange Liquidity Dashboard, Broker-Neutral Execution Architecture, Free-First Provider Strategy, Provider ROI Gates, Small Capital Growth Framework, and C++ Core Accelerators are implemented safely as future roadmap work only.

| Rank | Category | Future potential estimate |
| ---: | --- | ---: |
| 1 | Retail trading bot | 9.2/10 |
| 2 | Solo systematic trader platform | 8.4/10 |
| 3 | Small prop shop or small fund research stack | 7.2/10 |
| 4 | Top discretionary trader comparison | 6.8/10 |
| 5 | Institutional quant desk or enterprise control plane | 4.5/10 |
| 6 | HFT or elite execution platform | 2.3/10 |

Assumptions:

- AI agents and off-exchange liquidity remain research-only.
- Broker-neutral execution remains gated and manual until separately approved.
- No autonomous live-money trading is enabled.
- Category changes require the proof gates in `docs/TEN_OUT_OF_TEN_PROOF_GATES.md`, not roadmap intent.

## Safety Boundaries

- Ratings are current estimated readiness scores.
- Ratings are not proof of alpha.
- Ratings are not investor performance claims.
- Benchmark proof is required before claiming edge.
- Walk-forward proof is required before claiming repeatability.
- Paper-first safety remains the active execution boundary.
- Alpaca paper is the only unattended execution lane.
- Reward and forecast analytics are research-only.
- AI has no order authority.
- Risk gates remain authoritative.
- Broker routes remain unchanged.
- Live-money autonomy is not enabled.
- Promotion status is research metadata unless separately approved by a future explicit governance framework.
- No autonomous live-money orders.
- No risk-gate bypass.
- No kill-switch bypass.
- No automatic broker-route loosening.
- No automatic ranking-weight changes from reward analytics.
- Simulation evidence stays separate from real-time market-observed evidence.
- Support and review artifacts must exclude secrets, broker records, raw logs, account IDs, raw local paths, credentials, and unsanitized personal data.

## Build Vs Buy Boundary

Build anything that determines truth, edge, safety, or trust.

Preserve and improve in-house:

- Evidence schema.
- Event ledger.
- Candidate diagnostics.
- Missed move tracking.
- Evidence Reward.
- Forecast Validation.
- Forecast overlay contract.
- Rewardability contract.
- Strategy scoring logic.
- Ranking logic.
- Blocker logic.
- Risk gate logic.
- AI Evidence Referee boundaries.
- Professional Benchmark.
- Walk-Forward logic.
- Research Promotion logic.
- Score Calibration.
- Portfolio Risk Intelligence.
- Human vs System Shadow Mode.
- Audit UX.
- Why-trade explanation.
- Why-no-trade explanation.
- Support bundle sanitization.

Buy or use third-party commodity infrastructure only:

- Broker API connectivity.
- Raw market data vendors.
- Chart rendering libraries.
- Auth.
- Billing.
- Email.
- SMS.
- Hosting.
- Monitoring.
- Secret storage.
- CI/CD.
- Security review.
- Legal review.
- Compliance review.

## Product Surfaces And Desks

Current major surfaces include Strategies, Market Watchdog, Live Console, Candidate Diagnostics, Audit Replay, Risk Center, Execution Quality, Evidence Reward, Forecast Validation, Evidence Edge Analytics, Data Completeness, Professional Benchmark, Walk-Forward, Research Promotion, Score Calibration, Portfolio Risk, Human vs System Shadow Mode, and AI Committee.

Hedge Fund AI Agents v1 adds a read-only decision-support committee surface. It writes append-only sanitized research memos only, cannot place orders, cannot clear gates, cannot change broker routes, cannot change risk limits, cannot mutate ranking weights, and cannot approve live trading. See `docs/HEDGE_FUND_AI_AGENTS.md`.

Future expansion surfaces are documented in the roadmap backlog only. Market Specialist Desks are context engines, not order bots. The Visual Strategy Evidence Builder is a no-code evidence contract builder, not a no-code trading bot. The Off-Exchange Liquidity Dashboard is passive research context, not a trade trigger. Broker-neutral execution planning means Alpaca becomes one adapter, not that Quant Evidence OS becomes a broker. C++ Core Accelerators are performance helpers only and must not own trading authority.

The future market x strategy design should keep current strategy desks as the strategy layer and add market desks as a separate context layer. Candidate Fusion combines market context and strategy logic into one evidence-backed candidate while risk gates remain authoritative.

Current strategy desks:

- Macro Trend Desk.
- Stat Arb Desk.
- Equities Momentum Desk.
- Event-Driven Desk.
- Options Volatility Desk.

## How Every Category Reaches 10/10

The path to 10/10 is not one feature. It is a staged proof program:

1. Keep safety invariant and paper-first.
2. Make evidence complete enough to trust.
3. Prove edge against baselines.
4. Prove repeatability with frozen walk-forward tests.
5. Prove costs do not erase edge.
6. Prove rankings and features explain outcomes.
7. Prove portfolio and risk visibility.
8. Prove human-vs-system decision quality where claimed.
9. Add governance before firm claims.
10. Treat HFT as a separate future thesis.

## Upgrade Dependency Spine

No category should be marked 10/10 by opinion, UI completeness, or evidence volume alone. A category moves only when the relevant acceptance checklist items are complete and the required proof gates pass.

The proof-first discipline in `docs/PROOF_FIRST_ROADMAP_DISCIPLINE.md` controls priority before expansion. Ambition is allowed, but proof decides priority. Foundation work on Data Completeness, Professional Benchmark, Walk-Forward, Execution Quality, Score Calibration, Risk Gates, Audit Trail, Evidence Reward, Forecast Validation, Candidate Diagnostics, Portfolio Risk, Human vs System Shadow Mode, and Research Promotion takes priority over new desks, agents, broker expansion, HFT research, C++ accelerators, or enterprise features.

| Dependency | Categories primarily unlocked | Hard stop if missing |
| --- | --- | --- |
| Safety invariants | All categories | No rating upgrade, no stronger claims, and no autonomy expansion. |
| Data Completeness | Solo Systematic, Small Fund, Discretionary, Institutional | No benchmark, reward, forecast, or walk-forward claim can be trusted. |
| Professional Benchmark | Solo Systematic, Small Fund, Discretionary, Institutional, Retail proof language | No edge claim. |
| Walk-Forward | Solo Systematic, Small Fund, Discretionary, Institutional | No repeatability claim. |
| Execution Quality and TCA | Solo Systematic, Small Fund, Discretionary, Institutional | No tradability-after-costs claim. |
| Portfolio Risk | Small Fund, Institutional, Solo Systematic | No portfolio-aware or firm-facing risk claim. |
| Human vs System Shadow Mode | Top Discretionary Trader Comparison | No claim that the system beats skilled trader judgment. |
| Research Promotion and governance | Small Fund, Institutional | No team workflow, approval, or firm-readiness claim. |
| Lineage, permissions, audit, and external review | Institutional | No institutional-grade or compliance-adjacent claim. |
| HFT feasibility thesis | HFT | No HFT, DMA, or elite execution claim. |

## Category 1: Retail Trading Bot

Current estimated readiness: 9/10.

Why it has that rating:

Quant Evidence OS already exceeds most retail bots on paper-first safety, no-trade explanations, blockers, paper fills, missed opportunities, reconciliation state, forecast validation, and evidence review. The remaining gap is user comprehension and first-session usability.

What 10/10 means:

A non-technical user can start paper mode, understand every trade or no-trade decision, review missed opportunities, and export proof without touching code.

Missing capabilities:

- Guided onboarding.
- Demo evidence mode.
- Broker readiness wizard.
- Plain-language no-trade reports.
- Daily operator summary.
- Safe paper-only labels.
- Customer-safe empty states.
- Strategy explainers for each desk.
- Clean support export.
- Simple first-session checklist.
- Paper-mode health checklist.
- Clear action guidance when no trades happen.

Required engineering work:

- Define onboarding state machine and first-session checklist.
- Add demo evidence fixtures as synthetic/sample evidence only.
- Add support-export sanitization contract if not already complete.
- Add no-trade explanation coverage model across blockers, desks, and watchdog state.

Required data work:

- Define sample evidence fixtures that never mix with real-time market-observed evidence.
- Track no-trade explanation coverage, onboarding completion, and paper readiness states.
- Track support bundle completeness without secrets or raw local paths.

Required UI work:

- Guided paper-mode flow.
- Broker readiness wizard.
- Daily operator summary.
- Customer-safe empty states.
- Paper-only labels and proof labels.
- Desk-level plain-language explainers.

Required tests:

- Onboarding state transition tests.
- Demo evidence separation tests.
- Support bundle redaction tests.
- No-trade explanation coverage tests.
- Paper-only label visibility tests.

Required docs:

- First-session operator guide.
- Broker readiness guide.
- No-trade explanation guide.
- Support bundle safety guide.
- Paper-only proof language.

Required proof metrics:

- Time to first paper-ready state.
- Percentage of users who understand why no trade happened.
- Support bundle completeness.
- Onboarding completion rate.
- Paper readiness pass rate.
- No-trade explanation coverage.

Dependencies:

- Market Watchdog stability.
- Data Completeness enough for customer-safe explanations.
- Support bundle sanitization.
- Demo evidence boundaries.

Risks:

- Users mistake paper evidence for live-money proof.
- Demo evidence is confused with real-time market-observed evidence.
- Empty states expose internals or raw paths.

Acceptance criteria:

- A new user reaches paper-ready state through guided flow.
- Every no-trade state has a plain-language explanation and next safe action.
- Demo evidence is labeled synthetic/sample and cannot be counted as real-time market-observed evidence.
- Support export excludes secrets, account IDs, raw broker records, raw logs, and raw local paths.

What not to claim:

- Guaranteed returns.
- AI trading bot.
- Live autonomous money manager.
- Proven alpha.

Estimated priority: medium-high after proof-critical data and benchmark work.

Implementation order:

1. Paper-mode health checklist.
2. No-trade explanation coverage.
3. Support export sanitization.
4. Demo evidence mode.
5. Guided onboarding and strategy explainers.

## Category 2: Solo Systematic Trader Platform

Current estimated readiness: 7.5/10.

Why it has that rating:

The platform already has Evidence Reward, Forecast Validation, Evidence Edge, Data Completeness, Professional Benchmark, Walk-Forward, Score Calibration, and Execution Quality surfaces. The gap is repeatable proof after costs across frozen out-of-sample periods and multiple regimes.

What 10/10 means:

The system proves that higher-ranked candidates outperform lower-ranked candidates after costs, across frozen out-of-sample tests and multiple regimes.

Missing capabilities:

- Professional Benchmark maturity.
- Walk-Forward Experiment Registry maturity.
- Data Completeness maturity.
- Score Calibration maturity.
- Feature Attribution maturity.
- Experiment Registry versioning.
- Baseline Lab.
- Score bucket separation proof.
- Reward formula versioning.
- Forecast model versioning.
- Point-in-time feature snapshots.
- Out-of-sample reports.
- Cost-adjusted evaluation.
- Confidence interval reporting.
- Regime stability reporting.

Required engineering work:

- Harden benchmark report contracts.
- Freeze experiment snapshots with ranking formula, reward formula, forecast model, baseline definition, and feature versions.
- Add baseline lab semantics and conservative verdict rules.
- Add score bucket and feature attribution reporting.

Required data work:

- Complete forward returns, baselines, forecast actuals, slippage, spread, fill delay, regime labels, and feature timestamps.
- Preserve point-in-time feature snapshots.
- Separate simulation evidence from real-time market-observed evidence.

Required UI work:

- Benchmark summary with pass/fail proof status.
- Walk-forward experiment review.
- Score bucket separation view.
- Feature attribution review.
- Cost-adjusted edge view.

Required tests:

- No-lookahead tests.
- Forward-only outcome tests.
- Simulation separation tests.
- Baseline comparison tests.
- Walk-forward frozen snapshot tests.
- Score bucket lift tests.
- Analytics cannot mutate ranking weights tests.

Required docs:

- Benchmark methodology.
- Walk-forward methodology.
- Data completeness thresholds.
- Score calibration methodology.
- Feature attribution methodology.
- Cost model assumptions.

Required proof metrics:

- Baseline-relative edge.
- Score bucket lift.
- Walk-forward pass rate.
- Forecast accuracy.
- Reward by setup.
- Reward by engine.
- Reward by regime.
- Slippage-adjusted reward.
- Data completeness rate.
- Out-of-sample stability.
- Feature lift consistency.

Dependencies:

- Data Completeness.
- Professional Benchmark.
- Walk-Forward.
- Score Calibration and Feature Attribution.
- Execution Quality and TCA.

Risks:

- Raw evidence volume is mistaken for edge.
- Hindsight leakage enters benchmark data.
- Costs erase nominal signal quality.
- Ranking recommendations become treated as automatic configuration changes.

Acceptance criteria:

- Higher score buckets beat lower score buckets after costs in frozen walk-forward tests.
- Benchmark reports compare system results against same-window baselines.
- Walk-forward records are frozen before evaluation.
- Reward and forecast analytics remain research-only.

What not to claim:

- Proven alpha before benchmark and walk-forward gates pass.
- Repeatable edge before frozen out-of-sample stability exists.
- Automatic ranking improvement from reward analytics.

Estimated priority: highest.

Implementation order:

1. Data Completeness.
2. Professional Benchmark.
3. Walk-Forward.
4. Score Calibration and Feature Attribution.
5. Execution Quality and TCA.

## Category 3: Small Prop Shop Or Small Fund Research Stack

Current estimated readiness: 6/10.

Why it has that rating:

The platform has serious research surfaces, strategy desks, audit replay, paper evidence, risk surfaces, execution quality, benchmark layers, and research promotion concepts. It is not yet firm-ready because governance, role controls, registries, approval workflows, portfolio-level risk, and release controls need maturity.

What 10/10 means:

A small fund can review a strategy, inspect its evidence, approve or reject promotion, prove who changed what and when, and verify that risk controls stayed active.

Missing capabilities:

- Role-based access control.
- Operator role.
- Researcher role.
- Risk manager role.
- Admin role.
- Approval workflows.
- Strategy registry.
- Model registry.
- Feature registry.
- Configuration versioning.
- Immutable audit hardening.
- Portfolio-level risk.
- Transaction cost analysis maturity.
- Release validation.
- Rollback controls.
- Incident reports.
- Strategy promotion pipeline.
- Change history.
- Review queue.
- Research metadata permissions.

Required engineering work:

- Define RBAC policy model.
- Define approval workflow contracts.
- Define model, strategy, feature, and configuration registry contracts.
- Harden audit event immutability.
- Define release validation and rollback metadata.

Required data work:

- Store change metadata, reviewer identity, approval status, reason, version, evidence links, and rollback references.
- Link strategy promotion records to benchmark, walk-forward, data completeness, execution quality, and risk reports.

Required UI work:

- Review queue.
- Strategy registry.
- Model registry.
- Approval detail views.
- Portfolio risk review.
- Incident report view.
- Release validation summary.

Required tests:

- RBAC permission tests.
- Approval workflow tests.
- Audit immutability tests.
- Promotion does not alter execution tests.
- Rollback metadata tests.
- Portfolio risk coverage tests.

Required docs:

- Role model.
- Strategy promotion process.
- Model registry process.
- Release validation checklist.
- Incident response runbook.
- Audit evidence guide.

Required proof metrics:

- Who changed what and when.
- Strategy approval traceability.
- Model version traceability.
- Risk approval traceability.
- Audit event completeness.
- Portfolio risk coverage.
- Incident report completeness.
- Release validation pass rate.

Dependencies:

- Research Promotion.
- Portfolio Risk.
- Execution Quality.
- Professional Benchmark.
- Walk-Forward.
- RBAC and audit hardening.

Risks:

- Promotion status is mistaken for live approval.
- Governance exists visually but is not enforced.
- Audit records are editable or incomplete.
- Portfolio risk reports are not connected to promotion decisions.

Acceptance criteria:

- Every promotion decision links to evidence, benchmark, walk-forward, data, risk, and execution reports.
- Approval and rejection history is traceable.
- RBAC prevents unauthorized metadata changes.
- Promotion status remains research metadata unless a separate future governance framework explicitly approves more.

What not to claim:

- Firm-ready or fund-ready before RBAC, approvals, audit, and registry controls are proven.
- Compliance-approved.
- Managed-money readiness.

Estimated priority: high after solo systematic proof gates mature.

Implementation order:

1. Research Promotion maturity.
2. Portfolio Risk maturity.
3. Strategy and model registries.
4. RBAC and approval workflows.
5. Immutable audit and release controls.

## Category 4: Top Discretionary Trader Comparison

Current estimated readiness: 5/10.

Why it has that rating:

The system is strong at memory, consistency, blocker review, forecast validation, missed-move analysis, and evidence tracking. It has not proven that its actual judgment improves or beats a skilled trader on the same opportunity set.

What 10/10 means:

Across the same candidates, the system improves or beats a skilled trader's net decision quality after costs and risk adjustment.

Missing capabilities:

- Human vs System Shadow Mode maturity.
- Human thesis capture.
- Human direction capture.
- Human confidence capture.
- Human target capture.
- Human invalidation capture.
- Human expected horizon capture.
- System forecast comparison.
- Bias diagnostics.
- Override quality scoring.
- Human missed winner report.
- System missed winner report.
- Post-session trader review.
- Discretionary decision replay.
- Confidence calibration comparison.
- Human vs system benchmark report.

Required engineering work:

- Standardize human decision contracts.
- Match human and system records to the same candidate, timestamp, horizon, and cost assumptions.
- Add override outcome scoring and bias diagnostics.
- Add replay-friendly decision timelines.

Required data work:

- Capture human thesis, direction, confidence, target, invalidation, horizon, and optional override reason before outcomes.
- Store system comparison records separately from immutable candidate and forecast records.
- Track costs, risk adjustment, missed winners, and false positives/false negatives.

Required UI work:

- Human thesis capture form.
- Same-opportunity comparison dashboard.
- Post-session review.
- Override quality view.
- Missed winner report.
- Confidence calibration comparison.

Required tests:

- Human contract validation tests.
- Same-opportunity matching tests.
- No hindsight edit tests.
- Shadow comparison metric tests.
- No order-authority tests.

Required docs:

- Human vs System methodology.
- Trader review guide.
- Override quality definitions.
- Bias diagnostics definitions.

Required proof metrics:

- Human direction accuracy.
- System direction accuracy.
- Human target hit rate.
- System target hit rate.
- Human reward.
- System reward.
- Human false positive rate.
- System false positive rate.
- Human false negative rate.
- System false negative rate.
- Override quality.
- Missed winner rate.
- Risk-adjusted decision quality.

Dependencies:

- Human vs System Shadow Mode.
- Forecast Validation.
- Professional Benchmark.
- Execution Quality.
- Data Completeness.

Risks:

- Human records are entered after the move.
- Opportunity sets are not identical.
- Comparison ignores costs or risk.
- The system is overstated as better than skilled traders before proof.

Acceptance criteria:

- Human and system decisions are compared on the same candidates, timestamps, horizons, costs, and risk assumptions.
- Human records are captured before outcomes.
- Reports show where the human improved the system and where the system improved the human.
- No live-order authority is created.

What not to claim:

- The system beats skilled traders before same-opportunity-set evidence exists.
- The system replaces trader judgment.

Estimated priority: medium after benchmark and data completeness mature.

Implementation order:

1. Human decision contract.
2. Same-opportunity matching.
3. Post-session review.
4. Override scoring.
5. Human vs system benchmark report.

## Category 5: Institutional Quant Desk Or Enterprise Control Plane

Current estimated readiness: 3/10.

Why it has that rating:

The architecture points toward an audit-oriented control plane, but institutional evaluators need stronger data lineage, model lineage, feature lineage, permissions, approvals, environment separation, immutable audit, portfolio risk, deployment controls, incident handling, compliance review readiness, and firm-grade reporting.

What 10/10 means:

An institutional reviewer can inspect data lineage, model lineage, risk controls, approvals, evidence records, forecast records, reward outputs, and incident handling without relying on verbal explanation.

Missing capabilities:

- Point-in-time data layer.
- Survivorship-free universe.
- Corporate actions handling.
- Symbol change handling.
- Data vendor provenance.
- Feature generation timestamps.
- Model registry.
- Model lineage.
- Feature lineage.
- Approval workflows.
- Environment separation.
- Immutable audit logs.
- Portfolio exposure model.
- Factor exposure model.
- Liquidity exposure model.
- Stress testing.
- Scenario replay.
- Incident management.
- Release validation.
- Deployment rollback.
- Firm-grade reporting.
- Permissions.
- Compliance review readiness.

Required engineering work:

- Define lineage contracts for data, features, models, forecasts, rewards, benchmark runs, and walk-forward experiments.
- Define environment separation model.
- Define permission enforcement and audit rules.
- Define deployment, release validation, rollback, and incident records.

Required data work:

- Track vendor, timestamp, data freshness, feature generation time, model version, feature version, baseline definition version, universe version, and corporate-action assumptions.
- Store lineage links from strategy decisions to evidence, forecasts, outcomes, benchmark results, and risk records.

Required UI work:

- Lineage inspector.
- Permission and approval review.
- Incident report review.
- Firm-grade export.
- Portfolio/factor/liquidity/stress views.
- Release validation and rollback views.

Required tests:

- Permission enforcement tests.
- Audit immutability tests.
- Lineage completeness tests.
- Environment separation tests.
- Incident workflow tests.
- Stress report tests.
- Export sanitization tests.

Required docs:

- Data lineage guide.
- Model lineage guide.
- Feature lineage guide.
- Permission model.
- Incident management runbook.
- Release validation and rollback guide.
- Compliance readiness checklist.

Required proof metrics:

- Data lineage completeness.
- Feature lineage completeness.
- Model lineage completeness.
- Approval trace completeness.
- Audit immutability checks.
- Portfolio exposure coverage.
- Stress test coverage.
- Incident response completeness.
- Environment separation verification.
- Permission enforcement coverage.

Dependencies:

- Data Completeness.
- Walk-Forward.
- Professional Benchmark.
- Research Promotion.
- Portfolio Risk.
- RBAC.
- Immutable Audit.
- External security, legal, and compliance review planning.

Risks:

- Institutional-grade claims appear before external review.
- Lineage is partial or manually reconstructed.
- Permissions are documented but not enforced.
- Support exports expose sensitive data.

Acceptance criteria:

- A reviewer can trace a strategy decision from data source to feature generation, model/score version, forecast, reward, benchmark, walk-forward result, promotion decision, risk view, and incident history.
- Permissions and approvals are enforced and audited.
- Reports are sanitized and reviewable without raw secrets, raw logs, raw broker records, account IDs, or raw local paths.

What not to claim:

- Institutional-grade readiness.
- Compliance-approved status.
- Enterprise-complete control plane.
- Investment adviser status.

Estimated priority: later, after proof and small-fund governance.

Implementation order:

1. Lineage contracts.
2. RBAC and approvals.
3. Audit immutability.
4. Portfolio/factor/stress reporting.
5. Firm-grade reports and external review planning.

## Category 6: HFT Or Elite Execution Platform

Current estimated readiness: 2/10.

Why it has that rating:

This is not the current target. Quant Evidence OS is Alpaca-paper-first and evidence-oriented. It is not direct-market-access infrastructure and does not have exchange connectivity, colocation, order book reconstruction, queue position modeling, or ultra-low-latency execution controls.

What 10/10 means:

The system can compete in latency-sensitive execution with professional market infrastructure.

Missing capabilities if pursued:

- Direct market access.
- Exchange connectivity.
- Colocation.
- Low-latency market data.
- Order book reconstruction.
- Queue position modeling.
- Smart order routing.
- Latency monitoring.
- Nanosecond or microsecond timing controls.
- Exchange-grade kill switches.
- Execution research tooling.
- Market microstructure research.
- Venue analysis.
- Hardware and network monitoring.

Required engineering work:

- Separate HFT infrastructure thesis.
- Direct market data and execution connectivity architecture.
- Order book and queue modeling.
- Latency measurement and monitoring.
- Venue routing research.
- Exchange-grade kill switch design.

Required data work:

- Tick/order book data.
- Market data latency records.
- Order acknowledgment latency records.
- Venue fill data.
- Queue position evidence.
- Hardware and network telemetry.

Required UI work:

- HFT feasibility report only until separate thesis is approved.
- Latency and venue analysis surfaces only if the separate thesis proceeds.

Required tests:

- Deterministic replay tests.
- Latency distribution tests.
- Order book reconstruction tests.
- Queue model tests.
- Venue routing tests.
- Kill switch response tests.

Required docs:

- HFT feasibility study.
- Infrastructure thesis.
- Venue connectivity plan.
- Microstructure research plan.
- Safety design.

Required proof metrics:

- Latency distribution.
- Market data latency.
- Order acknowledgment latency.
- Fill probability.
- Queue position accuracy.
- Order book reconstruction quality.
- Venue routing performance.
- Execution cost vs venue.
- Kill switch response time.

Dependencies:

- Separate capital, infrastructure, vendor, regulatory, and execution thesis.
- External legal and compliance review.
- Dedicated execution engineering.

Risks:

- HFT language creates false product claims.
- Paper-first evidence platform is confused with elite execution infrastructure.
- Live execution pressure weakens current safety posture.

Acceptance criteria:

- The system competes in latency-sensitive execution with professional market infrastructure. This is not the recommended current product path.

What not to claim:

- HFT platform.
- Direct market access system.
- Elite execution platform.
- Low-latency trading product.

Estimated priority: future only.

Implementation order:

1. HFT feasibility study only.
2. Separate infrastructure thesis.
3. Separate approval, budget, vendor, legal, and compliance review.

Recommendation:

Do not target HFT first. Treat HFT as a separate future product thesis. Focus now on research, evidence, benchmark proof, walk-forward validation, risk visibility, execution quality, and trader decision intelligence.

## Cross-Category Build Sequence

### 1. Verification And Safety Audit

Purpose: prove the current system still respects paper-first and research-only boundaries.

Category impact: high for every category.

Dependencies: current safety docs, route inventory, existing tests, current runtime configuration.

Required backend work: plan-only review of service boundaries, route inventory, and safety invariants.

Required frontend work: plan-only review of safety labels, Market Watchdog, Live Console, and proof surfaces.

Required tests: safety invariant tests, route authorization tests, support export sanitization tests.

Required docs: safety audit checklist and verification report.

Acceptance criteria: no new trading authority, no live-money autonomy, no broker-route change, no risk-gate bypass, no ranking-weight mutation.

Safety constraints: all verification is read-only unless a future explicit fix is approved.

Estimated priority: P0.

What not to build yet: new execution paths, new broker routes, automatic kill-switch clearing.

### 2. Data Completeness Hardening

Purpose: make benchmark and walk-forward results trustworthy.

Category impact: high for Solo, Small Fund, Institutional; medium for Retail and Discretionary; low for HFT.

Dependencies: candidate lifecycle, forecasts, outcomes, paper fills, baselines, execution records.

Required backend work: completeness contracts and missing-field diagnostics.

Required frontend work: data readiness summaries and blocker explanations.

Required tests: missing forward return, baseline, slippage, regime, timestamp, and simulation-separation tests.

Required docs: data completeness thresholds and field definitions.

Acceptance criteria: configured reward fields are present above threshold, and incomplete evidence is not counted as proof.

Safety constraints: data fixes must not change trade authority.

Estimated priority: P0.

What not to build yet: automatic strategy promotion from completeness results.

### 3. Professional Benchmark Hardening

Purpose: prove or disprove edge against baselines after costs.

Category impact: high for Solo and Small Fund; medium for Retail, Discretionary, Institutional; low for HFT.

Dependencies: Data Completeness and cost fields.

Required backend work: benchmark methodology, baseline definitions, conservative verdicts.

Required frontend work: benchmark pass/fail proof views.

Required tests: same-window baselines, cost-adjustment, score bucket lift, missing evidence behavior.

Required docs: benchmark methodology and claim boundaries.

Acceptance criteria: no edge claim is allowed without benchmark evidence.

Safety constraints: benchmark outputs cannot submit orders or change ranking weights automatically.

Estimated priority: P0.

What not to build yet: live strategy promotion.

### 4. Walk-Forward Experiment Registry Maturity

Purpose: prove repeatability in frozen out-of-sample periods.

Category impact: high for Solo, Small Fund, Institutional; medium for Discretionary; low for Retail and HFT.

Dependencies: benchmark, data completeness, versioned formulas, feature snapshots.

Required backend work: frozen experiment snapshots and out-of-sample result records.

Required frontend work: experiment registry review and pass/fail state.

Required tests: frozen state, version immutability, no-lookahead, out-of-sample split tests.

Required docs: walk-forward methodology.

Acceptance criteria: repeatability claims require passed walk-forward records.

Safety constraints: experiment status is research metadata only.

Estimated priority: P0.

What not to build yet: automatic live rollout from walk-forward pass.

### 5. Score Calibration And Feature Attribution

Purpose: show whether scores and features explain outcomes.

Category impact: high for Solo and Discretionary; medium for Retail, Small Fund, Institutional; not relevant for HFT.

Dependencies: benchmark records and rewardable outcomes.

Required backend work: score bucket separation, feature lift, false positive/negative drivers.

Required frontend work: calibration and attribution review.

Required tests: score bucket lift, feature lift stability, manual-review-only recommendation tests.

Required docs: calibration methodology.

Acceptance criteria: recommendations stay manual-review-only and cannot mutate ranking weights automatically.

Safety constraints: no automatic ranking-weight changes.

Estimated priority: P1.

What not to build yet: self-tuning rankings.

### 6. Execution Quality And TCA Maturity

Purpose: prove forecasts are tradable after spread, slippage, delay, and fill risk.

Category impact: high for Solo and Small Fund; medium for Retail, Discretionary, Institutional; future only for HFT.

Dependencies: paper fills, quotes, spread, slippage, fill timing, route evidence.

Required backend work: cost model, paper-vs-expected comparison, execution-adjusted reward.

Required frontend work: TCA dashboards and per-order evidence links.

Required tests: cost adjustment, route separation, no order mutation, no broker route mutation.

Required docs: execution-quality methodology.

Acceptance criteria: edge survives costs before any edge claim.

Safety constraints: execution analytics cannot change order type, route, size, or submission.

Estimated priority: P1.

What not to build yet: smart order routing.

### 7. Portfolio Risk Intelligence Maturity

Purpose: make trade and strategy evidence visible at portfolio risk level.

Category impact: high for Small Fund and Institutional; medium for Solo and Discretionary; low for Retail; not relevant for HFT until separate thesis.

Dependencies: positions, candidates, strategy tags, exposure metadata.

Required backend work: exposure, concentration, factor, liquidity, drawdown, and stress models.

Required frontend work: portfolio risk review.

Required tests: portfolio risk cannot be bypassed by reward, forecast, AI, or simulation scores.

Required docs: portfolio risk definitions.

Acceptance criteria: every candidate can be reviewed with portfolio risk context.

Safety constraints: portfolio risk visibility cannot loosen risk gates.

Estimated priority: P1.

What not to build yet: automatic risk limit changes.

### 8. Human Vs System Shadow Mode Maturity

Purpose: compare skilled human judgment and system decisions on the same opportunities.

Category impact: high for Discretionary; medium for Solo and Small Fund; low for Retail and Institutional; not relevant for HFT.

Dependencies: data completeness, forecast validation, benchmark, execution costs.

Required backend work: human decision contracts and same-opportunity matching.

Required frontend work: thesis capture, replay, and comparison reports.

Required tests: pre-outcome capture, same-window comparison, no order authority.

Required docs: shadow mode methodology.

Acceptance criteria: human and system records are comparable after costs and risk adjustment.

Safety constraints: shadow mode remains research-only.

Estimated priority: P2.

What not to build yet: automatic human override execution.

### 9. Research Promotion Maturity

Purpose: organize evidence into manual promotion states without changing trading behavior.

Category impact: high for Small Fund and Institutional; medium for Solo; low for Retail and Discretionary; not relevant for HFT.

Dependencies: benchmark, walk-forward, data completeness, risk, execution quality.

Required backend work: promotion evidence links, status history, governance metadata.

Required frontend work: review queue and promotion detail.

Required tests: promotion status does not alter execution behavior.

Required docs: promotion process and claim boundaries.

Acceptance criteria: promotion status is research metadata unless separately approved by a future explicit governance framework.

Safety constraints: no live enablement from promotion status.

Estimated priority: P2.

What not to build yet: live promotion automation.

### 10. Retail Onboarding And Demo Evidence Mode

Purpose: make the product understandable for non-technical paper users.

Category impact: high for Retail; medium for Solo; low for others.

Dependencies: safety audit, support sanitization, no-trade explanations.

Required backend work: demo evidence contract and readiness states.

Required frontend work: onboarding, wizard, empty states, daily summary.

Required tests: demo separation, support export redaction, onboarding states.

Required docs: first-session guide and no-trade guide.

Acceptance criteria: a user can reach paper-ready state and understand decisions without code.

Safety constraints: demo evidence never counts as live-observed evidence.

Estimated priority: P2.

What not to build yet: live-money onboarding.

### 11. Governance, RBAC, Model Registry, And Approval Workflows

Purpose: prepare for small team and firm review.

Category impact: high for Small Fund and Institutional; medium for Solo; low for others.

Dependencies: promotion maturity, audit, registry definitions.

Required backend work: roles, permissions, registries, approvals.

Required frontend work: admin review views and approval workflow.

Required tests: permission enforcement and audit traceability.

Required docs: role model, registry process, approval process.

Acceptance criteria: who changed what and when is provable.

Safety constraints: roles cannot bypass risk gates or broker controls.

Estimated priority: P3.

What not to build yet: self-service institutional live deployment.

### 12. Institutional Data Lineage And Audit Hardening

Purpose: make the system reviewable by institutional evaluators.

Category impact: high for Institutional; medium for Small Fund; low for others.

Dependencies: data completeness, registries, RBAC, audit.

Required backend work: lineage contracts, environment separation, immutable audit, incident workflow.

Required frontend work: lineage inspector and firm-grade reporting.

Required tests: lineage completeness, permission enforcement, audit immutability.

Required docs: lineage, incident, release, and compliance readiness docs.

Acceptance criteria: a reviewer can inspect lineage and controls without verbal explanation.

Safety constraints: institutional reporting cannot imply compliance approval.

Estimated priority: P4.

What not to build yet: compliance-approved claims.

### 13. HFT Feasibility Study Only

Purpose: keep HFT separate from the current evidence platform.

Category impact: future only for HFT.

Dependencies: separate thesis, budget, vendors, legal/compliance review, dedicated execution engineering.

Required backend work: none for current product; feasibility analysis only.

Required frontend work: none for current product; feasibility analysis only.

Required tests: none until a separate thesis is approved.

Required docs: HFT feasibility study.

Acceptance criteria: HFT is not marketed or prioritized before a separate infrastructure thesis exists.

Safety constraints: do not weaken paper-first safety.

Estimated priority: future only.

What not to build yet: DMA, smart order routing, colocation, low-latency live execution.

## Long-Term Build Sequence With Future Expansion

This sequence captures current maturity work first and later expansion ideas second. It is a backlog order only. It does not implement features, alter runtime behavior, enable live trading, add broker routes, change risk gates, change order submission, or grant AI order authority.

The near-term order is governed by `docs/PROOF_FIRST_ROADMAP_DISCIPLINE.md`. Expansion items below remain deferred unless they pass the Safety Gate, Data Gate, Benchmark Gate, Walk-Forward Gate, and Expansion Justification Gate.

1. Finish current verification and safety audit.
2. Finish Data Completeness hardening.
3. Finish Professional Benchmark hardening.
4. Finish Walk-Forward maturity.
5. Finish Score Calibration and Feature Attribution.
6. Finish Execution Quality and TCA maturity.
7. Finish Portfolio Risk Intelligence maturity.
8. Finish Human vs System Shadow Mode maturity.
9. Finish Research Promotion maturity.
10. Add AI Committee Agents as research-only memos.
11. Add Market Specialist Desk registry.
12. Add Candidate Fusion Engine.
13. Add Market x Strategy Benchmark.
14. Add Off-Exchange Liquidity Dashboard as passive background research.
15. Add BrokerAdapter and MarketDataAdapter architecture.
16. Add Capability Registry and Route Eligibility Engine.
17. Add Broker Simulator Adapter.
18. Add ETF Proxy Registry.
19. Add Visual Strategy Evidence Builder.
20. Add Retail onboarding and demo evidence mode.
21. Add Pay Threshold and Provider ROI Gates.
22. Add Governance, RBAC, model registry, and approval workflows.
23. Add Institutional data lineage and audit hardening.
24. Add C++ Core Accelerators only after profiling proves bottlenecks.
25. Add HFT feasibility study only as a separate future thesis.

Future roadmap additions may improve category readiness if implemented safely, but they do not raise current ratings by themselves. The build vs buy boundary stays intact: build truth, edge, safety, trust, evidence contracts, risk boundaries, and authority controls in-house; buy commodity infrastructure only when proof and provider ROI gates justify it.

## Feature To Rating Impact Map

Impact labels: high, medium, low, not relevant, future only.

| Feature | Retail bot | Solo systematic trader | Small fund | Top discretionary trader | Institutional quant desk | HFT |
| --- | --- | --- | --- | --- | --- | --- |
| Data Completeness | medium | high | high | medium | high | low |
| Professional Benchmark | medium | high | high | medium | high | low |
| Walk-Forward | low | high | high | medium | high | low |
| Score Calibration | low | high | medium | medium | medium | not relevant |
| Feature Attribution | low | high | medium | medium | medium | not relevant |
| Execution Quality | medium | high | high | medium | medium | future only |
| Portfolio Risk | low | medium | high | medium | high | future only |
| Human vs System Shadow Mode | low | medium | medium | high | low | not relevant |
| Research Promotion | low | medium | high | low | high | not relevant |
| RBAC | low | low | high | low | high | not relevant |
| Model Registry | not relevant | medium | high | low | high | not relevant |
| Strategy Registry | low | medium | high | medium | high | not relevant |
| Immutable Audit | medium | medium | high | medium | high | low |
| Point-in-Time Data | low | high | high | medium | high | low |
| Survivorship-Free Universe | not relevant | high | high | low | high | low |
| Onboarding | high | medium | low | low | low | not relevant |
| Demo Evidence Mode | high | medium | low | low | low | not relevant |
| Broker Readiness Wizard | high | medium | medium | low | low | not relevant |
| Support Bundle Sanitization | high | medium | high | medium | high | low |
| Compliance Review | not relevant | low | medium | low | high | medium |
| HFT Feasibility Study | not relevant | not relevant | not relevant | not relevant | low | future only |

## Claims To Avoid

Avoid:

- Guaranteed returns.
- Proven alpha.
- AI trading bot.
- Autonomous money manager.
- Institutional-grade platform.
- Compliance-approved system.
- HFT platform.
- Direct market access system.
- Investment adviser.
- Black-box alpha machine.
- Live-trading ready system.

Allowed only when supported by current proof:

- Paper-first trading research platform.
- Trading evidence operating system.
- Forecast validation platform.
- Decision audit system.
- Research-to-risk workflow.
- Paper execution quality analysis.
- Structured strategy improvement system.
- Benchmark and walk-forward research layer.

## Highest Priority Build

The highest priority build is not HFT, live trading, new broker routing, new market desks, visual builders, provider expansion, or C++ acceleration. The highest priority is the proof chain:

1. Post-Implementation Verification.
2. Data Completeness cleanup.
3. Professional Benchmark hardening.
4. Walk-Forward validation.
5. Score Calibration and Feature Attribution.
6. Execution Quality and TCA.
7. Risk Gate and Audit Trail hardening.
8. Portfolio Risk cleanup.
9. Human vs System validation.
10. Research Promotion cleanup.
11. Only then revisit expansion features.

This sequence moves the strongest serious buyer category, Solo Systematic Trader Platform, toward stronger proof while also improving Retail, Small Fund, Discretionary, and Institutional readiness without changing trading behavior. It does not authorize expansion features before the proof-first gates in `docs/PROOF_FIRST_ROADMAP_DISCIPLINE.md` pass.

## Readiness Upgrade Rule

Before any category rating is raised, the owner of the rating must point to:

1. The completed checklist items in `docs/TEN_OUT_OF_TEN_ACCEPTANCE_CHECKLIST.md`.
2. The passed proof gates in `docs/TEN_OUT_OF_TEN_PROOF_GATES.md`.
3. The measurement window used for the claim.
4. The evidence source, data completeness state, and cost assumptions.
5. The remaining disallowed claims.

If any of those are missing, keep the rating as a current estimated readiness score and describe the work as roadmap progress, not readiness proof.
