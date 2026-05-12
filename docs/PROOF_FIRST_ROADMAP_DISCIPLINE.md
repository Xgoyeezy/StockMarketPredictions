# Proof-First Roadmap Discipline

## One-Sentence Principle

Ambition is allowed. Proof decides priority.

## Why This Document Exists

This document prevents feature creep in Quant Evidence OS / StockMarketPredictions by forcing roadmap work to prioritize proof, safety, and measurable decision quality before expansion.

Quant Evidence OS should not build another major layer until the current foundation proves it improves decision quality, safety, benchmark quality, or user trust. The system should prioritize evidence quality over feature count.

This is a planning document only. It does not implement features, add services, add routes, add pages, change execution behavior, enable live trading, change broker routes, modify order submission, modify risk gate logic, clear kill switches, grant AI order authority, merge simulation evidence into real-time market-observed evidence, or let analytics change ranking weights automatically.

## Current Foundation To Protect

The current foundation gets priority over expansion features:

- Data Completeness.
- Professional Benchmark.
- Walk-Forward.
- Execution Quality.
- Score Calibration.
- Risk Gates.
- Audit Trail.
- Evidence Reward.
- Forecast Validation.
- Candidate Diagnostics.
- Portfolio Risk.
- Human vs System Shadow Mode.
- Research Promotion.
- Technical Analysis evidence setup admission.

Foundation work is near-term only when it improves at least one of:

- Proof quality.
- Data completeness.
- Benchmark reliability.
- Walk-forward validity.
- Execution cost realism.
- Risk control.
- Auditability.
- Operator trust.

## Expansion Features To Defer

These ideas are deferred, not rejected. Preserve them in the roadmap, but keep them in future backlog unless they directly support the proof layer and pass the expansion gates in this document.

- Market Specialist Desks.
- Visual Strategy Evidence Builder.
- Hedge Fund AI Role Agents.
- Off-Exchange Liquidity Dashboard.
- Broker-Neutral Execution Architecture.
- Free-First Provider Strategy.
- Pay Threshold and Provider ROI Gates.
- Small Capital Growth Framework.
- C++ Core Accelerators.
- Institutional Governance.
- RBAC.
- Model Registry.
- Strategy Registry.
- HFT Feasibility Study.
- Latency-Aware Execution Research.

Expansion work is deferred when it mainly adds:

- More surface area.
- More dashboards.
- More agents.
- More asset classes.
- More broker complexity.
- More latency ambition.
- More architectural complexity.

without improving proof.

## Proof-First Backlog Filter

Every future feature should be scored before it moves into active work.

Use these rating labels:

- `high`
- `medium`
- `low`
- `negative`
- `unknown`

Score every feature on:

| Criterion | What to ask |
| --- | --- |
| Evidence impact | Does it make candidate, forecast, outcome, blocker, or fill evidence more complete and usable? |
| Benchmark impact | Does it improve baseline comparison, score bucket proof, or benchmark verdict quality? |
| Walk-forward impact | Does it improve frozen out-of-sample testing or reduce lookahead risk? |
| Execution quality impact | Does it improve spread, slippage, fill delay, alpha decay, or cost realism? |
| Risk reduction | Does it reduce operational, portfolio, strategy, broker, or data risk? |
| Auditability improvement | Does it make who/what/when/why evidence easier to inspect? |
| User trust improvement | Does it help an operator understand decisions and limits without overclaiming? |
| Implementation complexity | How much code, data, testing, and review does it require? |
| Dependency risk | Does it depend on vendors, broker behavior, secrets, live accounts, or unstable data? |
| Safety risk | Could it blur execution authority, broker routes, risk gates, AI authority, or ranking mutation? |
| Maintenance burden | Does it add long-term operational or support load? |

A feature should be near-term only if it improves at least one of:

- Proof quality.
- Data completeness.
- Benchmark reliability.
- Walk-forward validity.
- Execution cost realism.
- Risk control.
- Auditability.
- Operator trust.

A feature should be deferred if it mainly adds more surface area, dashboards, agents, asset classes, broker complexity, latency ambition, or architectural complexity without improving proof.

### Minimum Scoring Template

Use this template before moving any future feature into near-term planning:

| Feature | Evidence | Benchmark | Walk-forward | Execution quality | Risk reduction | Auditability | Trust | Complexity | Dependency risk | Safety risk | Maintenance | Decision |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Example feature | unknown | unknown | unknown | unknown | unknown | unknown | unknown | unknown | unknown | unknown | unknown | defer |

Default decision rules:

- `near-term`: At least one proof metric is `high`, safety risk is not `high`, and the smallest safe version is clear.
- `foundation-first`: Useful idea, but current proof layers need cleanup first.
- `future backlog`: Valuable later, but mostly expansion surface today.
- `reject for now`: Safety risk is high, proof impact is low or unknown, or rollback is unclear.

### Initial Expansion Backlog Scores

These scores preserve the expansion ideas without moving them into active work. They are planning labels only, not implementation approval.

| Feature | Evidence | Benchmark | Walk-forward | Execution quality | Risk reduction | Auditability | Trust | Complexity | Dependency risk | Safety risk | Maintenance | Decision |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Market Specialist Desks | medium | low | low | low | medium | low | medium | medium | low | medium | medium | future backlog |
| Visual Strategy Evidence Builder | medium | medium | medium | low | medium | medium | high | high | medium | high | high | future backlog |
| Hedge Fund AI Role Agents | medium | medium | low | low | medium | medium | medium | high | medium | high | high | future backlog |
| Off-Exchange Liquidity Dashboard | medium | low | low | medium | medium | medium | medium | high | high | medium | high | future backlog |
| Broker-Neutral Execution Architecture | low | low | low | high | medium | high | medium | high | high | high | high | future backlog |
| Free-First Provider Strategy | medium | medium | low | medium | medium | medium | high | medium | medium | low | medium | foundation-first docs only |
| Pay Threshold and Provider ROI Gates | high | medium | low | medium | high | high | high | medium | medium | low | medium | foundation-first docs only |
| Small Capital Growth Framework | medium | medium | medium | medium | high | medium | high | medium | low | medium | medium | foundation-first docs only |
| C++ Core Accelerators | low | low | low | low | low | medium | low | high | medium | medium | high | future backlog |
| Institutional Governance | medium | medium | low | low | high | high | high | high | medium | medium | high | future backlog |
| RBAC | low | low | low | low | high | high | high | high | medium | medium | high | future backlog |
| Model Registry | medium | medium | medium | low | medium | high | medium | high | medium | medium | high | future backlog |
| Strategy Registry | medium | medium | medium | low | medium | high | medium | high | medium | medium | high | future backlog |
| HFT Feasibility Study | low | low | low | medium | low | medium | low | high | high | high | high | future backlog |
| Latency-Aware Execution Research | low | low | low | medium | low | medium | low | high | high | high | high | future backlog |

Rules for these rows:

- `foundation-first docs only` means the idea may be documented as a proof discipline, cost discipline, or safety framing, but implementation remains gated.
- `future backlog` means no build work should start until the current foundation is cleaner and all expansion gates pass.
- Any row with `high` safety risk must remain inactive unless a separate future project proves the smallest safe version, rollback plan, and explicit human approval boundary.
- None of these rows authorize broker changes, order behavior changes, risk-gate changes, kill-switch changes, AI order authority, ranking-weight mutation, live trading, or deferred expansion implementation.

## Near-Term Priority Order

Use this priority order until the foundation is proof-clean:

1. Post-Implementation Verification
2. Data Completeness cleanup
3. Professional Benchmark hardening
4. Walk-Forward validation
5. Score Calibration and Feature Attribution
6. Execution Quality and TCA
7. Risk Gate and Audit Trail hardening
8. Portfolio Risk cleanup
9. Human vs System validation
10. Research Promotion cleanup
11. Only then revisit expansion features

## Expansion Gates

No expansion feature should move from future backlog to active work until these gates are answered and recorded.

### Gate 1, Safety Gate

Must prove:

- No live trading enabled.
- No broker route changes.
- No order logic changes.
- No risk gate bypass.
- No AI order authority.
- No ranking mutation.
- No secret exposure.

### Gate 2, Data Gate

Must prove:

- Required fields exist.
- Missing fields are measured.
- Rewardability rate is acceptable.
- Forecast validation has enough complete contracts.
- Benchmark inputs are usable.

### Gate 3, Benchmark Gate

Must prove:

- Baseline comparison exists.
- Score buckets are measured.
- Execution-adjusted reward exists.
- Insufficient evidence is handled honestly.

### Gate 4, Walk-Forward Gate

Must prove:

- Frozen rules.
- Out-of-sample test.
- No mid-test mutation.
- Walk-forward verdict exists.

### Gate 5, Expansion Justification Gate

Must answer:

- What exact proof problem does this feature solve?
- Which metric improves?
- Which category rating improves?
- What risk does it introduce?
- What is the smallest safe version?
- What is the rollback plan?

## What To Add Now

Add discipline, cleanup, and proof planning before adding major new product layers:

- Proof-first backlog scoring.
- Technical Analysis evidence setup admission contracts for objective, benchmarkable, executable-price methods only.
- Foundation-first roadmap.
- Feature freeze rule.
- Expansion gates.
- Current foundation priority.
- Proof metrics dashboard planning.
- Benchmark integrity checklist.
- Walk-forward integrity checklist.
- Data completeness cleanup plan.

Feature freeze rule:

- Do not add a major new layer until the foundation proves it improves decision quality, safety, benchmark quality, or user trust.
- Do not add expansion features just because they make the product look more advanced.
- Do not admit broad chart-lore methods as setup work unless they have causal rules, executable-price evidence, matched controls, walk-forward proof, cost survival, parameter stability, and provenance.
- Do not move an item out of future backlog without the expansion gates above.

Proof metrics dashboard planning should focus on:

- Data completeness rate.
- Rewardability rate.
- Benchmarkable candidate count.
- Forecast contract completeness.
- Baseline coverage.
- Execution-cost field coverage.
- Walk-forward frozen experiment count.
- Score bucket separation.
- Human vs System matched-record count.
- Risk and audit coverage.

Benchmark integrity checklist:

- Baselines exist and are same-window.
- Costs are included.
- Score buckets are measured.
- Missing data produces `insufficient_evidence`.
- Simulation evidence is not counted as real-time market-observed evidence.
- Results are not concentrated in one symbol, one session, or hindsight-selected setup.

Walk-forward integrity checklist:

- Rules are frozen before evaluation.
- Out-of-sample period is defined before outcomes are known.
- No mid-test mutation occurs.
- Data source, feature version, reward formula, score formula, and baseline definition are versioned.
- Verdict is pass, fail, or insufficient evidence.

Data completeness cleanup plan:

- Identify missing forward returns.
- Identify missing baselines.
- Identify missing forecast actuals.
- Identify missing spread, slippage, fill delay, and route evidence.
- Identify missing setup, engine, regime, and timestamp fields.
- Keep incomplete evidence visible, not fabricated.

## What To Defer

These are deferred, not rejected:

- New market desks.
- AI committee agents.
- Off-exchange liquidity dashboard.
- Broker-neutral live execution.
- New broker adapters.
- HFT work.
- C++ acceleration.
- Enterprise governance.
- RBAC.
- Model registry.
- Visual strategy builder.

Deferred means:

- Keep the idea in the roadmap.
- Do not delete prior notes.
- Do not claim the feature exists.
- Do not treat it as near-term unless it passes the expansion gates.
- Do not let it distract from current proof gaps.

## What To Avoid

Avoid:

- Building more desks before proving current desks.
- Building more agents before proving benchmark quality.
- Building broker expansion before execution quality is mature.
- Building C++ before profiling.
- Building HFT before direct market access thesis.
- Building live trading before walk-forward proof.
- Claiming alpha before proof.
- Claiming institutional-grade readiness before governance and review.
- Claiming HFT capability before infrastructure exists.

## Category Rating Impact

This discipline improves ratings by making proof stronger, not by adding more features.

| Category | Proof-first rating impact |
| --- | --- |
| Retail trading bot | Improves only if onboarding and explanation improve. More features alone do not raise the rating. |
| Solo systematic trader | Improves most from benchmark, walk-forward, data completeness, score calibration, and execution quality. |
| Small fund | Improves from audit, risk, evidence, and later governance. |
| Top discretionary trader | Improves from Human vs System validation and decision review. |
| Institutional | Improves from lineage, audit, governance, and controls, but not without proof. |
| HFT | Does not improve much until real infrastructure exists. |

## Safety Boundaries

This roadmap discipline does not change the current safety model.

Current safety model remains:

- Alpaca paper is the only unattended execution lane until explicitly changed in a separate future project.
- No autonomous live-money orders.
- No AI order authority.
- No risk-gate bypass.
- No kill-switch bypass.
- No automatic broker-route loosening.
- No automatic ranking-weight changes from reward analytics.
- Simulation evidence stays separate from real-time market-observed evidence.
- Forecast and reward analytics remain research-only.
- Expansion features are future roadmap items, not current implementation.
- AI agents, if built later, are decision-support analysts, not trading agents.
- Broker-neutral execution planning does not mean becoming a broker.
- C++ accelerators must not own trading authority.
- HFT remains future-only unless infrastructure proof exists.

## Decision Checklist Before Building Any New Feature

Before building any new feature, answer:

1. Is this foundation work or expansion work?
2. Which current proof gap does it solve?
3. Which metric improves?
4. Does it improve proof quality, data completeness, benchmark reliability, walk-forward validity, execution cost realism, risk control, auditability, or operator trust?
5. Does it add broker complexity, live execution pressure, AI authority, ranking mutation pressure, or data lineage risk?
6. Does it require new services, routes, pages, broker adapters, or execution behavior?
7. Can the smallest safe version be documented without changing runtime behavior?
8. What tests or review artifacts will prove it did not weaken safety?
9. What is the rollback plan?
10. If the feature does not improve proof, why should it not stay deferred?

If the answers are weak, keep the item in future backlog.
