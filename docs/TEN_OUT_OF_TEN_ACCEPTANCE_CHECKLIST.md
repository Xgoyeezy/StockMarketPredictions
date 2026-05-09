# Ten Out Of Ten Acceptance Checklist

This checklist defines concrete, verifiable readiness criteria for moving each Quant Evidence OS category toward 10/10. It is planning only. It does not implement services, add routes, add pages, modify execution, enable live trading, change broker routes, modify order submission, modify risk gates, clear kill switches, grant AI order authority, or let analytics change ranking weights automatically.

Ratings are current estimated readiness scores. They are not proof of alpha and are not investor performance claims. Benchmark proof is required before claiming edge. Walk-forward proof is required before claiming repeatability. Paper-first safety remains the active execution boundary. Reward and forecast analytics are research-only. AI has no order authority. Risk gates remain authoritative. Broker routes remain unchanged. Live-money autonomy is not enabled. Promotion status is research metadata unless separately approved by a future explicit governance framework.

## Checklist Use Rules

- A checkbox is complete only when there is a named evidence artifact, test result, report, or documented review backing it.
- UI completion does not satisfy proof readiness by itself.
- Evidence volume does not satisfy proof readiness by itself.
- A category cannot be called 10/10 unless its checklist items and required proof gates both pass.
- If a checklist item would require execution, broker, live-control, order, risk-gate, AI-authority, or ranking behavior changes, it must remain unchecked until a separate explicit implementation task is approved.
- Support, export, and firm-review artifacts must exclude secrets, broker records, raw logs, account IDs, raw local paths, credentials, and unsanitized personal data.

## Retail Trading Bot: 9/10 To 10/10

Product readiness:

- [ ] Guided onboarding reaches a paper-ready state without code changes.
- [ ] Paper-mode health checklist explains ready, blocked, watching, and killed states.
- [ ] Daily operator summary lists trades, no-trades, blockers, missed opportunities, and next safe action.

Data readiness:

- [ ] Demo evidence is clearly labeled synthetic/sample.
- [ ] Demo evidence is never counted as real-time market-observed evidence.
- [ ] No-trade records include blocker, desk, timestamp, next scan, and explanation.

Research readiness:

- [ ] Retail-facing summaries explain forecasts and rewards as research-only.
- [ ] Missed opportunities are reviewable without implying proven alpha.

Risk readiness:

- [ ] Paper-first label is visible on operator surfaces.
- [ ] Kill switch, loss lock, target lock, stale data, route block, and reconciliation blockers remain visible.

Execution readiness:

- [ ] Broker readiness wizard checks paper readiness without changing broker routes.
- [ ] Paper fills and rejected paper orders are explained in plain language.

Governance readiness:

- [ ] Support export excludes secrets, broker records, raw logs, account IDs, raw local paths, and credentials.
- [ ] User-facing proof labels distinguish paper evidence from live-money performance.

UI readiness:

- [ ] Customer-safe empty states explain why no data exists and what safe action comes next.
- [ ] Strategy explainers exist for Macro Trend, Stat Arb, Equities Momentum, Event-Driven, and Options Volatility desks.

Docs readiness:

- [ ] First-session checklist exists.
- [ ] No-trade explanation guide exists.
- [ ] Broker readiness guide exists.

Test readiness:

- [ ] Onboarding state transition tests exist.
- [ ] Demo evidence separation tests exist.
- [ ] Support bundle sanitization tests exist.

Proof readiness:

- [ ] Time to first paper-ready state is measured.
- [ ] No-trade explanation coverage is measured.
- [ ] Paper readiness pass rate is measured.

## Solo Systematic Trader Platform: 7.5/10 To 10/10

Product readiness:

- [ ] Benchmark, walk-forward, data completeness, score calibration, execution quality, and forecast validation can be reviewed together.
- [ ] Research views separate proof, missing evidence, and manual recommendations.

Data readiness:

- [ ] Required reward fields meet the configured threshold.
- [ ] Forward returns are stamped only after the configured horizon closes.
- [ ] Baseline forward returns are available for benchmarkable records.
- [ ] Feature snapshots include point-in-time generation timestamps.
- [ ] Simulation evidence stays separate from real-time market-observed evidence.

Research readiness:

- [ ] Score bucket 80 to 100 outperforms 60 to 80 after costs in frozen walk-forward tests.
- [ ] Professional Benchmark reports baseline-relative edge, score bucket lift, and cost-adjusted reward.
- [ ] Walk-forward experiments freeze ranking, reward, forecast, baseline, and feature versions before evaluation.
- [ ] Forecast validation reports direction accuracy, path error, timing error, and confidence calibration.

Risk readiness:

- [ ] Research recommendations cannot bypass risk gates.
- [ ] Risk gate states are included in candidate and benchmark context.

Execution readiness:

- [ ] Execution Quality reports slippage, spread, fill delay, and route evidence.
- [ ] Edge is reported before and after costs.

Governance readiness:

- [ ] Experiment versioning records formula, model, feature, baseline, and universe versions.
- [ ] Manual research recommendations are separated from configuration changes.

UI readiness:

- [ ] Score bucket separation is visible.
- [ ] Feature attribution is visible.
- [ ] Out-of-sample stability is visible.

Docs readiness:

- [ ] Benchmark methodology is documented.
- [ ] Walk-forward methodology is documented.
- [ ] Cost model assumptions are documented.

Test readiness:

- [ ] No-lookahead tests exist.
- [ ] Baseline comparison tests exist.
- [ ] Walk-forward frozen snapshot tests exist.
- [ ] Analytics cannot change ranking weights automatically.

Proof readiness:

- [ ] Baseline-relative edge is positive after costs.
- [ ] Walk-forward pass rate meets the configured threshold.
- [ ] Feature lift is stable across regimes.

## Small Prop Shop Or Small Fund Research Stack: 6/10 To 10/10

Product readiness:

- [ ] Research Promotion links strategies to benchmark, walk-forward, data, execution, and risk evidence.
- [ ] Review queue supports approve, reject, hold, and rollback metadata.

Data readiness:

- [ ] Strategy, model, feature, and configuration versions are recorded.
- [ ] Approval records link to exact evidence snapshots.

Research readiness:

- [ ] Promotion status requires benchmark and walk-forward evidence before paper-proven labels.
- [ ] Research metadata permissions prevent unauthorized status changes.

Risk readiness:

- [ ] Portfolio risk covers exposure, concentration, liquidity, drawdown, and stress context.
- [ ] Risk controls are shown as active or blocking during promotion review.

Execution readiness:

- [ ] Transaction cost analysis links orders to candidates, quotes, spread, slippage, route, receipt, fill, and reconciliation evidence.
- [ ] Execution analytics cannot submit orders or alter order settings.

Governance readiness:

- [ ] Operator, researcher, risk manager, and admin roles are defined.
- [ ] Role-based access control gates metadata changes.
- [ ] Approval workflows preserve who changed what and when.
- [ ] Incident reports and release validation records exist.

UI readiness:

- [ ] Review queue supports team workflow.
- [ ] Strategy registry and model registry are visible.
- [ ] Change history is visible.

Docs readiness:

- [ ] Role model is documented.
- [ ] Strategy promotion process is documented.
- [ ] Incident workflow is documented.

Test readiness:

- [ ] RBAC permission tests exist.
- [ ] Promotion status does not change execution behavior.
- [ ] Audit event immutability tests exist.

Proof readiness:

- [ ] Strategy approval traceability is complete.
- [ ] Model version traceability is complete.
- [ ] Portfolio risk coverage meets threshold.

## Top Discretionary Trader Comparison: 5/10 To 10/10

Product readiness:

- [ ] Human vs System Shadow Mode compares the same opportunity set.
- [ ] Post-session trader review summarizes wins, misses, overrides, and calibration.

Data readiness:

- [ ] Human thesis capture requires direction, confidence, target, invalidation, and horizon.
- [ ] Human decisions are timestamped before outcomes.
- [ ] System decisions are matched by candidate, timestamp, horizon, and cost model.

Research readiness:

- [ ] Human and system direction accuracy are compared.
- [ ] Human and system target hit rates are compared.
- [ ] Override quality is scored after costs and risk adjustment.

Risk readiness:

- [ ] Human override records include risk context.
- [ ] Shadow mode cannot bypass blockers, risk gates, or kill switches.

Execution readiness:

- [ ] Human and system reward comparisons include spread, slippage, and fill assumptions.
- [ ] Shadow mode does not submit or route orders.

Governance readiness:

- [ ] Human decision records are immutable after outcome windows close.
- [ ] Review notes are separated from execution authority.

UI readiness:

- [ ] Thesis capture UI requires direction, confidence, target, invalidation, and horizon.
- [ ] Human missed winner and system missed winner reports are visible.
- [ ] Bias diagnostics are visible.

Docs readiness:

- [ ] Human vs System methodology is documented.
- [ ] Override quality definitions are documented.

Test readiness:

- [ ] Same-opportunity matching tests exist.
- [ ] Pre-outcome capture tests exist.
- [ ] Shadow mode no-order-authority tests exist.

Proof readiness:

- [ ] System net decision quality beats or improves skilled human decisions after costs and risk adjustment.
- [ ] False positive and false negative rates are reported for both human and system.

## Institutional Quant Desk Or Enterprise Control Plane: 3/10 To 10/10

Product readiness:

- [ ] Institutional evaluator can inspect data lineage, model lineage, feature lineage, risk controls, approvals, forecasts, rewards, incidents, and audit evidence without verbal explanation.
- [ ] Firm-grade reports are sanitized and reproducible.

Data readiness:

- [ ] Point-in-time data layer exists.
- [ ] Survivorship-free universe is available.
- [ ] Corporate actions and symbol changes are handled or explicitly documented.
- [ ] Data vendor provenance is recorded.
- [ ] Feature generation timestamps are recorded.

Research readiness:

- [ ] Model registry records model lineage.
- [ ] Feature registry records feature lineage.
- [ ] Benchmark and walk-forward evidence link to data and model versions.

Risk readiness:

- [ ] Portfolio, factor, liquidity, concentration, drawdown, and stress reports exist.
- [ ] Risk controls are auditable and cannot be bypassed by analytics or AI.

Execution readiness:

- [ ] Execution reports link route, order, receipt, fill, reconciliation, slippage, and latency evidence where applicable.
- [ ] Execution analytics cannot alter broker routes or order behavior.

Governance readiness:

- [ ] Environment separation is verified.
- [ ] Permission enforcement coverage meets threshold.
- [ ] Approval trace completeness meets threshold.
- [ ] Incident response records are complete.
- [ ] Release validation and rollback controls are documented.

UI readiness:

- [ ] Lineage inspector is available.
- [ ] Permission review is available.
- [ ] Incident and release reports are available.

Docs readiness:

- [ ] Data lineage guide exists.
- [ ] Model lineage guide exists.
- [ ] Compliance readiness checklist exists.
- [ ] Incident management runbook exists.

Test readiness:

- [ ] Permission enforcement tests exist.
- [ ] Audit immutability tests exist.
- [ ] Lineage completeness tests exist.
- [ ] Environment separation tests exist.

Proof readiness:

- [ ] Data lineage completeness meets threshold.
- [ ] Model lineage completeness meets threshold.
- [ ] Audit immutability checks pass.
- [ ] External security, legal, and compliance review plan exists before any institutional-grade claim.

## HFT Or Elite Execution Platform: 2/10 To 10/10

Product readiness:

- [ ] HFT remains labeled future only unless a separate infrastructure thesis is approved.
- [ ] Current product avoids HFT and direct-market-access claims.

Data readiness:

- [ ] Feasibility study defines tick data, order book data, venue data, and latency telemetry requirements.
- [ ] No current evidence-control-plane metric is presented as HFT proof.

Research readiness:

- [ ] Market microstructure research plan exists before any build.
- [ ] Venue analysis plan exists before any build.

Risk readiness:

- [ ] Exchange-grade kill switch requirements are documented for future study only.
- [ ] Current risk gates remain unchanged.

Execution readiness:

- [ ] Direct market access, colocation, smart routing, and queue modeling are not claimed as current capabilities.
- [ ] Latency-sensitive execution is treated as a separate future product thesis.

Governance readiness:

- [ ] Legal, compliance, vendor, and capital requirements are listed before any HFT work.
- [ ] Separate approval is required before HFT infrastructure work.

UI readiness:

- [ ] Current UI does not imply HFT capability.
- [ ] Any HFT mention is clearly feasibility-only.

Docs readiness:

- [ ] HFT feasibility study exists before implementation.
- [ ] HFT claims-to-avoid language exists.

Test readiness:

- [ ] No current test treats paper evidence as HFT proof.
- [ ] Future-only test plan covers latency distribution, order book reconstruction, queue model, and kill switch response.

Proof readiness:

- [ ] Latency distribution, market data latency, order acknowledgement latency, fill probability, queue position accuracy, venue routing performance, and kill switch response time are defined as future proof metrics only.
