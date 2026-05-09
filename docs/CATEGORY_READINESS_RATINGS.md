# Category Readiness Ratings

Quant Evidence OS, implemented as `StockMarketPredictions`, is a paper-first trading evidence and research operating system that turns scans, decisions, blockers, forecasts, paper outcomes, missed moves, and operational state into reviewable evidence.

These ratings are current estimated readiness scores for positioning and roadmap planning. They are not official ratings, objective industry rankings, investor performance claims, or proof of alpha. Benchmark proof is required before claiming edge, and walk-forward proof is required before claiming repeatability.

## Current Product Context

Quant Evidence OS is not just a trading bot. The current product shape is a Python/FastAPI plus React/Vite trading research, paper-automation, and evidence-control-plane system.

Current surfaces include Market Watchdog, Live Console, Strategies, Candidate Diagnostics, Audit Replay, Risk Center, Execution Quality, Evidence Reward, Forecast Validation, Evidence Edge Analytics, Data Completeness, Professional Benchmark, Walk-Forward, Research Promotion, Score Calibration, Portfolio Risk, and Human vs System Shadow Mode.

Current strategy desks:

- Macro Trend Desk.
- Stat Arb Desk.
- Equities Momentum Desk.
- Event-Driven Desk.
- Options Volatility Desk.

The platform scans equities and ETFs, runs paper-first strategy desks, ranks paper candidates, supervises hard risk gates, explains why trades or no-trades happened, tracks blockers and missed opportunities, reviews AI evidence, tracks paper orders and fills, reconciles route state, validates forecasts, scores evidence rewards, evaluates execution quality, and turns decisions into reusable evidence.

## Current Safety Boundaries

- Alpaca paper is the only unattended execution lane.
- No autonomous live-money orders.
- No AI order authority.
- No risk-gate bypass.
- No kill-switch bypass.
- No automatic broker-route loosening.
- No automatic ranking-weight changes from reward analytics.
- Simulation evidence stays separate from real-time market-observed evidence.
- Forecast and reward analytics are research-only.
- Live-control surfaces may exist, but live submission is not enabled autonomous money management.
- Broker routes remain unchanged by analytics, forecasts, rewards, benchmarks, or AI review.
- Risk gates remain authoritative.
- Support and positioning artifacts must not expose secrets, broker records, raw logs, account IDs, raw local paths, credentials, or unsanitized personal data.

## 10/10 Upgrade Planning References

The full path from current estimated readiness to 10/10 is defined in `docs/TEN_OUT_OF_TEN_CATEGORY_UPGRADE_MASTER_PLAN.md`. The concrete checkboxes are in `docs/TEN_OUT_OF_TEN_ACCEPTANCE_CHECKLIST.md`, the near-term sequence is in `docs/TEN_OUT_OF_TEN_30_60_90_DAY_PLAN.md`, and rating upgrades are gated by `docs/TEN_OUT_OF_TEN_PROOF_GATES.md`.

These references preserve the same safety language as this ratings document: ratings are current estimated readiness scores, ratings are not proof of alpha, ratings are not investor performance claims, benchmark proof is required before claiming edge, walk-forward proof is required before claiming repeatability, paper-first safety remains the active execution boundary, reward and forecast analytics are research-only, AI has no order authority, risk gates remain authoritative, broker routes remain unchanged, live-money autonomy is not enabled, and promotion status is research metadata unless separately approved by a future explicit governance framework.

## Overall Category Ranking

| Rank | Category | Current estimated readiness | Current interpretation |
| ---: | --- | ---: | --- |
| 1 | Retail trading bot | 9/10 | Strongest current category because the system explains decisions, blockers, paper route state, missed opportunities, and evidence better than typical retail automation. |
| 2 | Solo systematic trader platform | 7.5/10 | Best serious-user category because the system already has the research loop, but still needs proof that rankings beat baselines after costs. |
| 3 | Small prop shop or small fund research stack | 6/10 | Promising for small teams, but governance, role controls, registry discipline, and firm-grade audit are not mature enough yet. |
| 4 | Top discretionary trader comparison | 5/10 | Stronger than humans at memory and evidence tracking, but not yet proven against skilled trader judgment on the same opportunity set. |
| 5 | Institutional quant desk or enterprise control plane | 3/10 | Directionally correct architecture, but institutional data lineage, governance, permissions, and compliance evidence are still missing. |
| 6 | HFT or elite execution platform | 2/10 | Not the right target; the system is Alpaca-paper-first and evidence-oriented, not direct-market-access execution infrastructure. |

## Retail Trading Bot: 9/10

Reason:

Most retail bots focus on entries, exits, and automation. Quant Evidence OS explains why trades happened, why trades did not happen, what blockers fired, what forecasts were tested, what paper fills looked like, and what evidence was created. The paper-first posture makes it safer and easier to trust than most retail automation tools.

What keeps it from 10/10:

- Guided onboarding.
- Demo evidence mode.
- Simpler broker setup.
- Better customer-safe empty states.
- Clearer no-trade reports.
- Daily operator summary.
- Plain-language strategy explanations.
- Clean support export.
- Paper-only proof labels.

Target 10/10 condition:

A non-technical user can run paper mode, understand every decision, review missed opportunities, and export proof without touching code.

## Solo Systematic Trader Platform: 7.5/10

Reason:

This is the best serious-user category. A solo systematic trader cares about evidence, repeatability, ranking quality, forecast validation, execution quality, benchmark comparison, and post-session review. The platform already has the right direction through Evidence Reward, Forecast Validation, Evidence Edge, Data Completeness, Professional Benchmark, Walk-Forward, Score Calibration, and Execution Quality.

What keeps it from 10/10:

- Statistically significant alpha proof after costs.
- Stronger walk-forward validation.
- Cleaner experiment versioning.
- Feature attribution.
- Score bucket separation proof.
- Baseline-relative performance reports.
- Point-in-time feature snapshots.
- Reward formula versioning.
- Forecast model versioning.
- Cost-adjusted evaluation.
- Out-of-sample stability reports.

Target 10/10 condition:

The system proves that higher-ranked candidates outperform lower-ranked candidates after costs, across frozen out-of-sample tests and multiple regimes.

## Small Prop Shop Or Small Fund Research Stack: 6/10

Reason:

The system is promising for small professional teams because it has strategy desks, audit replay, paper evidence, risk surfaces, execution quality, benchmark layers, and research promotion concepts. It is more serious than a retail bot, but it is not yet firm-ready.

What keeps it from 10/10:

- Role-based access control.
- Approval workflows.
- Model registry.
- Strategy registry.
- Immutable audit hardening.
- Portfolio-level risk.
- Deployment controls.
- Incident reports.
- Governance workflows.
- Operator roles.
- Researcher roles.
- Risk manager roles.
- Admin roles.
- Configuration versioning.
- Release validation.
- Rollback controls.

Target 10/10 condition:

A small fund can review a strategy, inspect its evidence, approve or reject promotion, prove who changed what and when, and verify that risk controls stayed active.

## Top Discretionary Trader Comparison: 5/10

Reason:

The system can beat humans in memory, consistency, evidence tracking, blocker review, forecast validation, and missed-move analysis. It has not yet proven that its actual judgment beats a skilled trader on the same opportunity set.

What keeps it from 10/10:

- Mature Human vs System Shadow Mode.
- Human thesis capture.
- Human confidence capture.
- Human target capture.
- Human invalidation capture.
- Human expected horizon capture.
- System forecast comparison.
- Bias diagnostics.
- Override quality scoring.
- Post-session trader review.
- Human missed winner report.
- System missed winner report.

Target 10/10 condition:

Across the same candidates, the system improves or beats a skilled trader's net decision quality after costs and risk adjustment.

## Institutional Quant Desk Or Enterprise Control Plane: 3/10

Reason:

The architecture is directionally correct because it includes audit, evidence, paper controls, risk surfaces, execution quality, benchmark readiness, and research promotion. Institutional buyers need much stronger proof, governance, data lineage, model lineage, permissions, deployment control, compliance review, and firm-grade reporting.

What keeps it from 10/10:

- Point-in-time data.
- Survivorship-free universe.
- Corporate actions handling.
- Symbol change handling.
- Data vendor provenance.
- Feature generation timestamps.
- Model registry.
- Model lineage.
- Feature lineage.
- Approval workflows.
- Factor exposure.
- Portfolio risk.
- Immutable audit logs.
- Environment separation.
- Incident management.
- Compliance review.
- Firm-grade reporting.
- Permissions.

Target 10/10 condition:

An institutional reviewer can inspect data lineage, model lineage, risk controls, approvals, evidence records, forecast records, reward outputs, and incident handling without relying on verbal explanation.

## HFT Or Elite Execution Platform: 2/10

Reason:

This is not the right target yet. The system is Alpaca-paper-first and evidence-oriented. It is not a direct-market-access, co-located, ultra-low-latency execution platform.

What keeps it from 10/10:

- Direct market access.
- Exchange connectivity.
- Colocation.
- Order book reconstruction.
- Queue position modeling.
- Low-latency market data.
- Smart order routing.
- Latency monitoring.
- Nanosecond or microsecond timing controls.
- Exchange-grade kill switches.
- Execution research team-level tooling.

Target 10/10 condition:

The system can compete in latency-sensitive execution with professional market infrastructure.

Recommendation:

Do not target HFT first. Focus on research, evidence, forecast validation, risk, benchmark proof, walk-forward validation, and execution quality for intraday and swing timeframes.

## Best Current Positioning

Best current positioning:

```text
Trading evidence and research operating system.
```

Secondary positioning:

- Paper-first trading control plane.
- Forecast validation and decision audit platform.
- Systematic trader evidence layer.
- Research-to-risk workflow for serious traders.

## Best First Serious Buyer

The best first serious buyer is a solo systematic trader or advanced retail trader who wants proof, paper automation, forecast validation, execution quality, benchmark comparison, and decision review.

This buyer can tolerate paper-first operation, understand evidence quality, and value reviewability before the system has full firm governance. Small prop shops and small funds are plausible later buyers, but they need stronger approval workflows, registry discipline, portfolio risk, permissions, and audit hardening.

## Best Next Proof Milestone

The best next proof milestone is Professional Benchmark plus Walk-Forward results.

Until that proves repeatable edge after costs, the platform should be described as a strong evidence-control platform, not a proven alpha machine.

## Claims To Avoid

- Guaranteed profit system.
- AI trading bot.
- HFT platform.
- Live autonomous money manager.
- Replacement for broker controls.
- Investment adviser.
- Black-box alpha machine.
- Institutional-grade platform unless proof exists.
- Compliance-approved system.
- Proven alpha system.
- Proven professional alpha.
- Enterprise-complete control plane.

## Build Vs Buy Boundary

Preserve in-house core logic.

In-house areas:

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

Third-party acceptable areas:

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

Rule:

Build anything that determines truth, edge, safety, or trust. Buy commodity infrastructure only.

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

## Proof Language

Use this language consistently:

- Ratings are current estimated readiness scores.
- Ratings are not proof of alpha.
- Ratings are not investor performance claims.
- Benchmark proof is required before claiming edge.
- Walk-forward proof is required before claiming repeatability.
- Paper-first safety remains the active execution boundary.
- Reward and forecast analytics are research-only.
- AI has no order authority.
- Risk gates remain authoritative.
- Broker routes remain unchanged.
- Live-money autonomy is not enabled.
