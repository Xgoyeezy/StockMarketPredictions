# Ten Out Of Ten Proof Gates

These proof gates define what must be true before Quant Evidence OS readiness claims can be upgraded. This is planning only and does not implement services, add routes, add pages, change execution behavior, enable live trading, change broker routes, modify order submission, modify risk gate logic, clear kill switches, grant AI order authority, merge simulation evidence into real-time market-observed evidence, or let analytics change ranking weights automatically.

Ratings are current estimated readiness scores. They are not proof of alpha and are not investor performance claims. Benchmark proof is required before claiming edge. Walk-forward proof is required before claiming repeatability. Paper-first safety remains the active execution boundary. Reward and forecast analytics are research-only. AI has no order authority. Risk gates remain authoritative. Broker routes remain unchanged. Live-money autonomy is not enabled. Promotion status is research metadata unless separately approved by a future explicit governance framework.

## Gate Summary

| Gate | Name | Primary claim controlled |
| ---: | --- | --- |
| 1 | Safety intact | The platform remains paper-first and research-only where required. |
| 2 | Data complete enough | Evidence is complete enough to measure. |
| 3 | Benchmark available | Baseline comparisons exist. |
| 4 | Baselines beaten | Edge can be discussed cautiously. |
| 5 | Walk-forward passed | Repeatability can be discussed cautiously. |
| 6 | Execution costs handled | Tradability after costs can be discussed cautiously. |
| 7 | Risk visibility complete | Portfolio-level risk review can be discussed cautiously. |
| 8 | Governance complete | Small-fund workflow claims can be discussed cautiously. |
| 9 | External review complete where needed | Institutional or compliance-adjacent claims can be discussed cautiously. |

## Gate Order And Category Dependency Matrix

Gates are cumulative unless a category explicitly says otherwise. Passing a later gate does not excuse a failure in an earlier safety, data, benchmark, or cost gate.

| Category | Gates required before 10/10 | Extra category-specific proof |
| --- | --- | --- |
| Retail trading bot | Gate 1 plus relevant Gate 2 evidence | Non-technical paper onboarding, no-trade explanation coverage, demo evidence separation, and sanitized support export. |
| Solo systematic trader platform | Gates 1-6 | Higher-ranked candidates beat lower-ranked candidates after costs across frozen out-of-sample tests and multiple regimes. |
| Small prop shop or small fund research stack | Gates 1-8 | Team roles, approvals, registries, promotion traceability, portfolio risk, release validation, and rollback evidence. |
| Top discretionary trader comparison | Gates 1-6 | Same-opportunity human-vs-system comparison after costs and risk adjustment. |
| Institutional quant desk or enterprise control plane | Gates 1-9 | Data lineage, model lineage, feature lineage, environment separation, permissions, incident handling, firm-grade reporting, and external review evidence. |
| HFT or elite execution platform | Separate future thesis beyond Gates 1-9 | Direct market access, exchange connectivity, colocation, low-latency market data, order book reconstruction, queue modeling, venue analysis, and exchange-grade controls. |

## Gate 1: Safety Intact

Entry criteria:

- Current safety docs, route inventory, execution boundaries, risk gate docs, and live-control docs are reviewed.
- Tests or review artifacts can identify broker route, order submission, risk gate, AI authority, ranking, and simulation/evidence boundaries.

Pass criteria:

- No autonomous live-money orders are enabled.
- Alpaca paper remains the only unattended execution lane.
- AI has no order authority.
- Reward and forecast analytics are research-only.
- Broker routes remain unchanged.
- Risk gates remain authoritative.
- Kill switches are not auto-cleared.
- Simulation evidence stays separate from real-time market-observed evidence.

Failure criteria:

- Any analytics surface can place orders, change routes, alter risk gates, clear kill switches, or mutate ranking weights automatically.
- Any support or report artifact exposes secrets, broker records, raw logs, account IDs, raw local paths, or credentials.

Required evidence:

- Safety verification report.
- Route inventory.
- Relevant test results.
- Support export sanitization evidence.

Categories affected:

- All categories.

Claims allowed:

- Paper-first trading research platform.
- Trading evidence operating system.

Claims still disallowed:

- Proven alpha.
- Live-trading ready system.
- Autonomous money manager.
- Institutional-grade platform.
- HFT platform.

## Gate 2: Data Complete Enough

Entry criteria:

- Candidate, forecast, blocker, missed move, paper fill, execution, and benchmark records are available for review.
- Data Completeness thresholds are defined.

Pass criteria:

- Required reward fields meet the configured threshold.
- Forward returns, baselines, forecast actuals, slippage, spread, fill delay, and regime labels are present where required.
- Missing evidence is visible and blocks proof claims.
- Simulation evidence is visible but not counted as real-time market-observed evidence.

Failure criteria:

- Missing forward returns or baselines prevent benchmark evaluation.
- Feature or forecast records lack timestamps.
- Simulation evidence is merged into real-time market-observed evidence.

Required evidence:

- Data Completeness report.
- Missing-field report.
- Rewardability report.
- Simulation separation evidence.

Categories affected:

- Retail, Solo Systematic, Small Fund, Discretionary, Institutional.

Claims allowed:

- Evidence readiness can be discussed for covered fields.

Claims still disallowed:

- Edge.
- Repeatability.
- Proven alpha.

## Gate 3: Benchmark Available

Entry criteria:

- Gate 1 and Gate 2 pass.
- Professional Benchmark inputs are complete enough for baseline comparison.

Pass criteria:

- Baselines are computed for the same universe, session, tradability filters, and cost assumptions.
- Reports include baseline-relative edge, score bucket lift, reward by setup, reward by engine, reward by regime, and missing-field status.

Failure criteria:

- Baselines use hindsight selection.
- Baselines are missing for material segments.
- Costs are omitted.

Required evidence:

- Professional Benchmark report.
- Baseline definitions.
- Cost assumptions.
- Score bucket report.

Categories affected:

- Solo Systematic, Small Fund, Institutional, Discretionary, Retail.

Claims allowed:

- Benchmark research layer.
- Baseline comparison available.

Claims still disallowed:

- Edge, unless Gate 4 passes.
- Repeatability, unless Gate 5 passes.

## Gate 4: Baselines Beaten

Entry criteria:

- Gate 3 passes.
- Enough benchmarkable records exist for configured sample-size threshold.

Pass criteria:

- System outcomes beat simple baselines after costs.
- Higher score buckets outperform lower score buckets.
- Results are not concentrated in a single hindsight-selected symbol, day, or regime.
- Confidence intervals or stability diagnostics are reported.

Failure criteria:

- Edge is pre-cost only.
- Score buckets do not separate.
- Positive results are too small, too sparse, or too concentrated.

Required evidence:

- Baseline-relative edge report.
- Score bucket lift report.
- Cost-adjusted reward report.
- Stability diagnostics.

Categories affected:

- Solo Systematic, Small Fund, Discretionary, Institutional.

Claims allowed:

- Cautious benchmark edge language for paper research, if the report supports it.

Claims still disallowed:

- Proven alpha.
- Repeatable edge.
- Investor performance claims.
- Live trading readiness.

## Gate 5: Walk-Forward Passed

Entry criteria:

- Gate 4 passes.
- Walk-forward experiments have frozen snapshots before evaluation.

Pass criteria:

- Frozen out-of-sample tests pass configured thresholds.
- Ranking formula, reward formula, forecast model, baseline definition, feature version, market universe, and data source are versioned.
- Results hold across multiple regimes where claimed.

Failure criteria:

- Experiment records are changed after outcomes.
- Out-of-sample results fail.
- Results depend on a single regime while claims imply broad repeatability.

Required evidence:

- Walk-Forward Experiment Registry records.
- Frozen snapshot metadata.
- Out-of-sample result reports.
- Regime stability reports.

Categories affected:

- Solo Systematic, Small Fund, Institutional, Discretionary.

Claims allowed:

- Cautious repeatability language for the tested paper research scope.

Claims still disallowed:

- Proven alpha.
- Guaranteed returns.
- Live-money autonomy.

## Gate 6: Execution Costs Handled

Entry criteria:

- Gate 3 passes.
- Paper fill, spread, slippage, fill delay, and route evidence are available.

Pass criteria:

- Edge survives spread, slippage, delay, and fill risk assumptions.
- Execution quality reports link candidate, quote, spread, route, receipt, fill, and reconciliation evidence.
- Execution analytics remain read-only.

Failure criteria:

- Cost-adjusted reward is negative or unavailable.
- Fill quality evidence is missing.
- Execution analytics change orders, routes, size, or risk settings.

Required evidence:

- Execution Quality and TCA report.
- Cost-adjusted benchmark report.
- Paper-vs-expected fill report.

Categories affected:

- Solo Systematic, Small Fund, Institutional, Discretionary, Retail.

Claims allowed:

- Paper execution quality analysis.
- Cost-adjusted paper research where supported.

Claims still disallowed:

- Elite execution platform.
- HFT platform.
- Smart order routing capability unless separately built and proven.

## Gate 7: Risk Visibility Complete

Entry criteria:

- Portfolio records, strategy tags, candidate records, and risk settings are available for review.

Pass criteria:

- Portfolio exposure, concentration, liquidity, drawdown, factor, sector, and stress context are visible.
- Risk gates remain authoritative.
- Reward, forecast, AI, and simulation scores cannot override risk controls.

Failure criteria:

- Candidate review lacks portfolio context where category claims require it.
- Portfolio risk analytics alter risk limits automatically.

Required evidence:

- Portfolio Risk report.
- Stress and scenario reports.
- Risk gate state evidence.

Categories affected:

- Small Fund, Institutional, Solo Systematic, Discretionary.

Claims allowed:

- Portfolio risk visibility and research-to-risk workflow where supported.

Claims still disallowed:

- Risk-managed live autonomy.
- Broker control replacement.

## Gate 8: Governance Complete

Entry criteria:

- Research Promotion, audit, registry, and role requirements are defined.

Pass criteria:

- Operator, researcher, risk manager, and admin roles are enforced.
- Strategy, model, feature, and configuration registries are versioned.
- Approval workflows prove who changed what and when.
- Promotion status remains research metadata unless separately approved by a future explicit governance framework.

Failure criteria:

- Governance records are editable without trace.
- Promotion status changes execution behavior.
- Unauthorized users can alter research metadata.

Required evidence:

- RBAC test results.
- Audit immutability checks.
- Approval trace reports.
- Registry records.

Categories affected:

- Small Fund and Institutional.

Claims allowed:

- Small-team research workflow claims where supported.

Claims still disallowed:

- Compliance-approved.
- Institutional-grade.
- Managed-money readiness.

## Gate 9: External Review Complete Where Needed

Entry criteria:

- Gate 8 passes for firm-facing claims.
- Security, legal, compliance, and vendor requirements are scoped.

Pass criteria:

- External security review plan or result exists.
- Legal and compliance review plan or result exists.
- Firm-grade reports are sanitized.
- Environment separation and permission enforcement are verified.

Failure criteria:

- Institutional language is used without proof and review.
- Reports expose sensitive data.
- Environment separation is unverified.

Required evidence:

- External review artifacts or review plan.
- Compliance readiness checklist.
- Security review plan.
- Sanitized firm-grade report.

Categories affected:

- Institutional and, if ever pursued, HFT.

Claims allowed:

- Compliance readiness planning, if accurate.
- Institutional-readiness roadmap, if still not claiming completion.

Claims still disallowed:

- Compliance-approved system unless formally approved.
- Institutional-grade platform unless proof, controls, and reviews support the claim.
- HFT platform unless a separate HFT thesis is built and proven.

## Rating Upgrade Policy

- Retail 10/10 requires Gate 1 plus paper onboarding, no-trade explanation coverage, support sanitization, and demo evidence separation.
- Solo Systematic 10/10 requires Gates 1 through 6.
- Small Fund 10/10 requires Gates 1 through 8.
- Discretionary Comparison 10/10 requires Gates 1 through 6 plus same-opportunity Human vs System proof.
- Institutional 10/10 requires Gates 1 through 9 plus lineage, permissions, environment separation, incident handling, and firm-grade reporting.
- HFT 10/10 requires a separate future infrastructure thesis beyond these evidence-platform gates.
