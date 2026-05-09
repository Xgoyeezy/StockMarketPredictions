# Ten Out Of Ten Roadmap

This roadmap describes how Quant Evidence OS can move from an Alpaca-paper-first evidence platform toward 10/10 readiness in each professional category. It is a planning document only. It does not implement trading behavior, change broker routes, enable live trading, modify order submission, bypass risk gates, grant AI order authority, or let analytics mutate ranking weights automatically.

The canonical category matrix is `docs/CATEGORY_READINESS_RATINGS.md`. Ratings are current estimated readiness scores, not official industry ratings, proof of alpha, or investor performance claims.

## Roadmap Guardrails

This roadmap is documentation and planning only. It must not be used as implicit approval to implement roadmap features, enable live trading, add broker routes, change order submission logic, change risk gates, clear kill switches, grant AI order authority, or let analytics change ranking weights automatically.

Simulation evidence remains separate from real-time market-observed evidence. Forecast and reward analytics are research-only until separately reviewed. Benchmark proof is required before claiming edge, and walk-forward proof is required before claiming repeatability.

## Master Planning References

This roadmap is summarized into a full category upgrade plan in `docs/TEN_OUT_OF_TEN_CATEGORY_UPGRADE_MASTER_PLAN.md`. Use `docs/TEN_OUT_OF_TEN_ACCEPTANCE_CHECKLIST.md` for concrete 10/10 checkboxes, `docs/TEN_OUT_OF_TEN_30_60_90_DAY_PLAN.md` for the near-term build sequence, and `docs/TEN_OUT_OF_TEN_PROOF_GATES.md` for the proof required before any rating or claim is upgraded.

Hedge Fund AI Agents v1 is documented in `docs/HEDGE_FUND_AI_AGENTS.md`. It is a read-only decision-support committee layer that writes append-only sanitized research memos only. It does not add trading authority, order authority, broker-route authority, risk-gate authority, kill-switch authority, risk-limit authority, live-trading approval, or automatic ranking-weight changes.

The same safety language applies to every stage: ratings are current estimated readiness scores, ratings are not proof of alpha, ratings are not investor performance claims, benchmark proof is required before claiming edge, walk-forward proof is required before claiming repeatability, paper-first safety remains the active execution boundary, reward and forecast analytics are research-only, AI has no order authority, risk gates remain authoritative, broker routes remain unchanged, live-money autonomy is not enabled, and promotion status is research metadata unless separately approved by a future explicit governance framework.

## Current Category Ratings

| Rank | Category | Current estimated readiness | 10/10 target condition |
| ---: | --- | ---: | --- |
| 1 | Retail trading bot | 9/10 | A non-technical user can run paper mode, understand every decision, review missed opportunities, and export proof without touching code. |
| 2 | Solo systematic trader platform | 7.5/10 | Higher-ranked candidates outperform lower-ranked candidates after costs across frozen out-of-sample tests and multiple regimes. |
| 3 | Small prop shop or small fund research stack | 6/10 | A small fund can review strategy evidence, approve or reject promotion, prove who changed what and when, and verify risk controls stayed active. |
| 4 | Top discretionary trader comparison | 5/10 | Across the same candidates, the system improves or beats a skilled trader's net decision quality after costs and risk adjustment. |
| 5 | Institutional quant desk or enterprise control plane | 3/10 | An institutional reviewer can inspect lineage, controls, approvals, evidence, forecasts, rewards, and incidents without relying on verbal explanation. |
| 6 | HFT or elite execution platform | 2/10 | The system can compete in latency-sensitive execution with professional market infrastructure. This is not the recommended first target. |

## Category Improvement Map

| Category | Main roadmap dependencies |
| --- | --- |
| Retail trading bot | Guided onboarding, demo evidence mode, customer-safe empty states, paper-only labels, daily operator summary, plain-language no-trade reports, clean support export. |
| Solo systematic trader platform | Professional Benchmark, Walk-Forward, Data Completeness, Score Calibration, Feature Attribution, Forecast Validation, Evidence Reward, execution-adjusted outcomes. |
| Small prop shop or small fund research stack | Governance, RBAC, approval workflows, model and strategy registry, config versioning, portfolio risk, release validation, rollback controls, incident reports. |
| Top discretionary trader comparison | Human vs System Shadow Mode, human thesis contracts, confidence, targets, invalidation, horizon, override scoring, bias diagnostics, post-session review. |
| Institutional quant desk or enterprise control plane | Point-in-time data, survivorship-free universe, corporate actions, symbol changes, vendor provenance, lineage, permissions, immutable audit, compliance review. |
| HFT or elite execution platform | Direct market access, exchange connectivity, colocation, order book reconstruction, queue position modeling, low-latency market data, smart order routing, latency monitoring. |

## Priority Logic

1. Prove edge before scaling autonomy.
2. Make every result reproducible and forward-only.
3. Expand from trade-level safety to portfolio-level risk intelligence.
4. Prove tradability after costs, not just signal correctness.
5. Add governance before firm-facing claims.
6. Compare against skilled humans on the same opportunity set.
7. Package the product with clear paper-only proof and no overclaims.

## Proof Dependencies

| Proof dependency | Needed before claiming |
| --- | --- |
| Professional Benchmark results | Any edge claim against baselines. |
| Walk-Forward results | Repeatability across frozen out-of-sample periods. |
| Cost-adjusted execution quality | Tradability after spread, slippage, delay, and fill risk. |
| Data completeness and point-in-time checks | Reliable historical and forward-only evaluation. |
| Score calibration and feature attribution | Ranking quality and explainable score separation. |
| Human vs System Shadow Mode | Claims against skilled discretionary trader judgment. |
| Governance, RBAC, and immutable audit hardening | Small fund, institutional, or enterprise control-plane claims. |

## Stage 1: Proof Layer

Goal: prove which signals, forecasts, blockers, engines, regimes, and score buckets have edge.

Required builds:

- Evidence Reward Engine hardening.
- Forecast Validation Engine hardening.
- Baseline Lab.
- Walk-Forward Validator.
- Experiment Registry.
- Score Bucket Validator.
- Blocker Value Report.
- AI Verdict Accuracy Report.
- Missed Move Recovery Report.

Why it matters:

- Converts raw evidence into measurable research.
- Separates signal claims from baseline-adjusted outcome proof.
- Prevents "more evidence" from being confused with "better edge."
- Gives solo systematic traders and reviewers a reason to trust rankings.

Ratings improved:

- Retail bot: 9/10 to 10/10 when reports are understandable.
- Solo systematic trader: 7.5/10 toward 9/10.
- Top discretionary trader comparison: 5/10 toward 6/10 through better review.
- Small prop shop or small fund research stack: 6/10 toward 7/10.

Build vs buy:

- Build in-house: rewardability rules, baseline comparison, blocker value, score bucket validation, experiment registry semantics, no-lookahead checks, and evidence-to-reward connection.
- Buy or use libraries: statistical utilities, chart rendering, storage helpers, test infrastructure.

Acceptance criteria:

- Every rewardable row has a timestamped pre-move prediction contract.
- Every forecast validation result is stored separately from immutable forecast records.
- Baselines are visible beside system outcomes.
- Reward recommendations are manual-review only.
- Simulation evidence stays separate from live-observed evidence.

Technical dependencies:

- Candidate lifecycle records.
- Evidence Accelerator records.
- Market-day reports.
- Paper trade books.
- Forward return fields or follow-up windows.
- Forecast overlay records.

Risk controls:

- No ranking weight mutation.
- No execution config mutation.
- No broker route mutation.
- No AI order authority.

## Stage 2: Data Layer

Goal: make every result reproducible, forward-only, and audit-safe.

Required builds:

- Point-in-time market data handling.
- Corporate actions support.
- Survivorship-free symbol universe.
- Quote freshness tracking.
- Feature timestamps.
- Data provenance.
- Missing data diagnostics.
- Data quality score.
- Market session metadata.

Why it matters:

- Without point-in-time data, backtests and reward reports can accidentally use unavailable future information.
- Without corporate actions and universe history, long-term comparisons become biased.
- Without feature timestamps, forecast and reward records cannot prove they were known before the move.

Ratings improved:

- Solo systematic trader: toward 10/10.
- Small fund research: toward 8/10.
- Institutional quant desk or enterprise control plane: 3/10 toward 5/10.

Build vs buy:

- Build in-house: data provenance model, feature timestamps, quality score, missing data diagnostics, and forward-only enforcement.
- Use third-party vendors: raw market data, corporate actions feeds, symbol master data, and storage infrastructure.

Acceptance criteria:

- Each feature has a generation timestamp and source.
- Each outcome uses only data after prediction creation.
- Reports state data vendor, freshness, gaps, and fallback state.
- Survivorship and corporate-action assumptions are visible.

Risk controls:

- Stale or missing data remains a blocker.
- Data fallback does not imply signal readiness.
- No reward is assigned when required forward data is missing.

## Stage 3: Risk Layer

Goal: move from trade-level safety to portfolio-level risk intelligence.

Required builds:

- Portfolio exposure model.
- Gross exposure and net exposure.
- Sector exposure.
- Factor exposure.
- Beta exposure.
- Correlation heat.
- Liquidity exposure.
- Single-name concentration.
- Strategy concentration.
- Drawdown state.
- Volatility targeting.
- Scenario stress testing.

Why it matters:

- A trade can be individually valid but portfolio-dangerous.
- Serious traders and small funds need risk explained by strategy, setup, engine, regime, blocker, forecast confidence, sector, factor, and correlation heat.

Ratings improved:

- Solo systematic trader: toward 9.5/10.
- Small fund research: toward 8.5/10.
- Institutional quant desk or enterprise control plane: 3/10 toward 6/10.

Build vs buy:

- Build in-house: product-facing risk layer, exposure grouping, heat explanations, risk gate display, and evidence links.
- Use third-party libraries: covariance math, factor definitions, numerical utilities, and reporting charts.

Acceptance criteria:

- Every paper candidate shows trade-level and portfolio-level risk context.
- Portfolio risk cannot be overridden by reward, forecast, AI, or simulation scores.
- Stress reports explain what would break the account objective or loss budget.

Risk controls:

- Kill switch, loss lock, target lock, stale data, route block, reconciliation block, cooldown, and daily risk gates remain authoritative.

## Stage 4: Execution Layer

Goal: prove that a forecast is tradable, not only correct.

Required builds:

- Slippage model.
- Spread model.
- Fill probability model.
- Alpha decay model.
- Partial fill analysis.
- Time-to-fill analysis.
- Execution quality score.
- Transaction cost analysis dashboard.
- Paper vs expected fill comparison.
- Execution-adjusted reward.

Why it matters:

- A forecast can be directionally correct and still not be tradable after spread, slippage, delay, and fill risk.
- Execution quality is often the difference between research edge and real trading value.

Ratings improved:

- Solo systematic trader: toward 10/10.
- Small fund research: toward 8.5/10.
- Institutional quant desk or enterprise control plane: 3/10 toward 6.5/10.
- HFT/elite execution platform: 2/10 only marginally, because this is still not DMA or colocation.

Build vs buy:

- Build in-house: execution-adjusted reward, paper-vs-expected comparison, alpha decay rules, and evidence links.
- Use third-party vendors: broker receipts, raw fill data, exchange calendars, and latency monitoring infrastructure.

Acceptance criteria:

- Reward reports show pre-cost and post-cost outcomes.
- Execution quality reports link each order to candidate, quote, spread, route, receipt, fill, and reconciliation evidence.
- Poor fillability can lower research confidence but cannot bypass risk or force order changes.

Risk controls:

- Execution analytics cannot submit orders.
- Execution analytics cannot change order type, route, or size automatically.

## Stage 5: Governance Layer

Goal: make the platform credible for small funds, prop shops, and enterprise review.

Required builds:

- Model registry.
- Strategy registry.
- Feature registry.
- Approval workflows.
- Role-based access control.
- Immutable audit logs.
- Config versioning.
- Release validation.
- Rollback controls.
- Kill switch test reports.
- Incident reports.
- Environment separation.

Why it matters:

- Firm use requires knowing who changed what, why, when, with what evidence, and how it was approved.
- Governance turns a research workstation into a reviewable control plane.

Ratings improved:

- Small fund research: toward 10/10.
- Institutional quant desk or enterprise control plane: 3/10 toward 7/10.
- Retail bot: customer trust improves, but this is not the main retail value driver.

Build vs buy:

- Build in-house: evidence-linked approvals, strategy promotion, config versioning semantics, audit UX, release gates, and incident evidence.
- Use third-party infrastructure: auth providers, identity management, secret storage, logging infrastructure, CI/CD, security review, compliance review.

Acceptance criteria:

- Strategies, models, features, forecasts, reward formulas, and configs have versions.
- Promotion cannot happen without evidence and approval.
- Audit reports can prove that risk controls remained active.
- Support bundles stay sanitized.

Risk controls:

- No approval workflow can enable live autonomy unless a separate live-control project explicitly adds it and all risk/legal conditions pass.

## Stage 6: Human Comparison Layer

Goal: measure the system against skilled trader judgment on the same opportunity set.

Required builds:

- Human vs System Shadow Mode.
- Human thesis capture.
- Human forecast contract.
- Human confidence tracking.
- Human target and invalidation tracking.
- Human override scoring.
- Bias diagnostics.
- Session review reports.
- System-vs-human benchmark.

Why it matters:

- The system can already remember more and audit better than humans.
- To claim it beats human judgment, it must compare against humans under the same inputs, costs, timing, risk, and opportunity set.

Ratings improved:

- Top discretionary trader: toward 10/10.
- Solo systematic trader: stronger confidence in decision rules.
- Product positioning: stronger demo story without overclaiming.

Build vs buy:

- Build in-house: human thesis contract, comparison logic, override quality scoring, bias diagnostics, and review reports.
- Use third-party tools: UI components, charting, and note editor infrastructure.

Acceptance criteria:

- Humans and system both submit timestamped contracts before outcomes are known.
- Same baselines, costs, and invalidation rules apply.
- Reports separate skill, luck, cost, timing, and risk-adjusted outcome.

Risk controls:

- Human override notes do not bypass gates.
- Human comparison results do not mutate ranking weights automatically.

## Stage 7: Product Layer

Goal: make the platform sellable without overclaiming.

Required builds:

- Guided onboarding.
- Demo evidence mode.
- Broker readiness wizard.
- Safe paper-only labels.
- Daily operator summary.
- Support bundle export.
- Customer-safe dashboards.
- Documentation.
- Pricing tiers.
- Landing page proof claims.
- Compliance-safe language.

Why it matters:

- The product is strongest when customers can see proof quickly.
- Buyers should understand why the system is different without hearing live-alpha claims.

Ratings improved:

- Retail bot: to 10/10.
- Solo systematic trader: improves adoption.
- Small fund research: improves diligence.

Build vs buy:

- Build in-house: demo evidence flow, support bundle sanitization, proof claims, Watchdog copy, and buyer-specific docs.
- Use third-party infrastructure: billing, auth, email, SMS, hosting, analytics, support desk, and legal/compliance review.

Acceptance criteria:

- Demo shows why a trade happened, why no trade happened, what blockers protected the system, what blockers missed winners, whether forecasts were correct, whether scores separated outcomes, whether AI reviews helped, whether execution quality supported the signal, and what should improve next.
- Copy avoids guaranteed returns, AI trading bot, HFT platform, live autonomous money manager, investment adviser, or black-box alpha machine claims.

## Priority Order

1. Professional Benchmark Suite and Baseline Lab.
2. Walk-Forward Validator and frozen-parameter rule.
3. Evidence Reward and Forecast Validation hardening.
4. Score Bucket Validator and Blocker Value Report.
5. Transaction Cost Analysis and execution-adjusted reward.
6. Point-in-time data and provenance.
7. Portfolio risk intelligence.
8. Experiment Registry and versioning.
9. Governance, RBAC, model registry, and audit hardening.
10. Human vs System Shadow Mode.
11. Guided onboarding, demo evidence mode, and product packaging.

## Recommended Next Work Order

1. Verify safety invariants and test status.
2. Run or inspect Professional Benchmark Suite.
3. If insufficient evidence, improve Data Completeness.
4. If data quality is weak, fix missing forward returns, baselines, forecast actuals, slippage, and regime labels.
5. If no edge is detected, improve Score Calibration and Feature Attribution.
6. If weak edge is detected, move to Walk-Forward validation.
7. If edge is detected, validate through Walk-Forward before any promotion.
8. Only after proof, improve Research Promotion and firm-readiness governance.
9. Do not pursue HFT until a separate infrastructure thesis exists.

## Cross-Cutting Safety Requirements

- Alpaca paper remains the only unattended execution lane.
- No autonomous live-money orders.
- No AI order authority.
- No risk-gate bypass.
- No kill-switch bypass.
- Rewards, forecasts, benchmark reports, and human comparison reports remain analytics until separately reviewed.
- Simulation evidence remains separate from real-time market-observed evidence.
- Support bundles remain sanitized.
- Institutional and HFT language remains constrained until the proof exists.
