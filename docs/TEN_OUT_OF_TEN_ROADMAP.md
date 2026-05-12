# Ten Out Of Ten Roadmap

This roadmap describes how Quant Evidence OS can move from an Alpaca-paper-first evidence platform toward 10/10 readiness in each professional category. It is a planning document only. It does not implement trading behavior, change broker routes, enable live trading, modify order submission, bypass risk gates, grant AI order authority, or let analytics mutate ranking weights automatically.

The canonical category matrix is `docs/CATEGORY_READINESS_RATINGS.md`. Ratings are current estimated readiness scores, not official industry ratings, proof of alpha, or investor performance claims.

Proof-first roadmap discipline is defined in `docs/PROOF_FIRST_ROADMAP_DISCIPLINE.md`. Ambition is allowed. Proof decides priority.

## Roadmap Guardrails

This roadmap is documentation and planning only. It must not be used as implicit approval to implement roadmap features, enable live trading, add broker routes, change order submission logic, change risk gates, clear kill switches, grant AI order authority, or let analytics change ranking weights automatically.

Simulation evidence remains separate from real-time market-observed evidence. Forecast and reward analytics are research-only until separately reviewed. Benchmark proof is required before claiming edge, and walk-forward proof is required before claiming repeatability.

Quant Evidence OS should not build another major layer until the current foundation proves it improves decision quality, safety, benchmark quality, or user trust. Evidence quality takes priority over feature count.

## Master Planning References

This roadmap is summarized into a full category upgrade plan in `docs/TEN_OUT_OF_TEN_CATEGORY_UPGRADE_MASTER_PLAN.md`. Use `docs/PROOF_FIRST_ROADMAP_DISCIPLINE.md` for the feature-freeze and expansion-gate discipline, `docs/TEN_OUT_OF_TEN_ACCEPTANCE_CHECKLIST.md` for concrete 10/10 checkboxes, `docs/TEN_OUT_OF_TEN_30_60_90_DAY_PLAN.md` for the near-term build sequence, and `docs/TEN_OUT_OF_TEN_PROOF_GATES.md` for the proof required before any rating or claim is upgraded.

Hedge Fund AI Agents v1 is documented in `docs/HEDGE_FUND_AI_AGENTS.md`. It is a read-only decision-support committee layer that writes append-only sanitized research memos only. It does not add trading authority, order authority, broker-route authority, risk-gate authority, kill-switch authority, risk-limit authority, live-trading approval, or automatic ranking-weight changes.

Technical Analysis evidence setup admission is documented in `docs/TECHNICAL_ANALYSIS_EVIDENCE_SETUP_RESEARCH.md`. It narrows setup backlog work to objective, executable-price, benchmarkable, walk-forward-testable method families. Momentum/trend, volume, support/resistance, dynamic support/resistance, breakouts, reversals, market structure, and BOS may be documented as evidence-only setup candidates. More interpretive methods stay research-only or avoid unless future proof gates clear.

Proof Metrics Dashboard v1 is documented in `docs/PROOF_METRICS_DASHBOARD.md`. It is a read-only proof visibility layer that aggregates gaps across Data Completeness, Evidence Outcomes, Professional Benchmark, Walk-Forward, Score Calibration, Evidence Reward, Execution Quality, Risk/Audit, Portfolio Risk, Forecast Validation, Shadow Mode, Research Promotion, and AI Committee safety. It does not approve expansion work, live trading, broker changes, risk-gate changes, kill-switch changes, or ranking-weight mutation.

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
- Technical Analysis evidence setup admission contracts.
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

## Future Expansion Backlog

This section captures strategic roadmap ideas only. These items are future backlog or gated expansion items under `docs/PROOF_FIRST_ROADMAP_DISCIPLINE.md`. They are not current implementation, do not change runtime behavior, and do not authorize feature work without a separate future project, safety review, proof-gate update, and expansion-gate justification.

These roadmap items do not change the current safety model.

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
- Market Specialist Desks are context engines, not order bots.
- AI agents are decision-support analysts, not trading agents.
- Off-Exchange Liquidity Dashboard is research context, not a trade trigger.
- Broker-neutral execution planning does not mean becoming a broker.
- C++ accelerators must not own trading authority.

### Future Best Positioning

Future roadmap positioning, if the items below are implemented safely and proven:

```text
Trading evidence and research operating system with AI committee review, market specialist desks, forecast validation, off-exchange liquidity intelligence, and broker-neutral execution planning.
```

Still avoid:

- Guaranteed returns.
- Proven alpha.
- AI trading bot.
- HFT platform.
- Autonomous live-money manager.
- Investment adviser.
- Black-box alpha machine.
- Institutional-grade platform without proof.

### Future Potential Category Ratings

These are future potential estimates, not current implementation ratings. They are not proof of alpha and are not investor performance claims. They assume AI agents and off-exchange liquidity remain research-only, broker-neutral execution remains gated and manual until separately approved, and no autonomous live-money trading is enabled.

| Category | Future potential estimate with proposed additions |
| --- | ---: |
| Retail trading bot | 9.2/10 |
| Solo systematic trader platform | 8.4/10 |
| Small prop shop or small fund research stack | 7.2/10 |
| Top discretionary trader comparison | 6.8/10 |
| Institutional quant desk or enterprise control plane | 4.5/10 |
| HFT or elite execution platform | 2.3/10 |

### Market Specialist Desks

Market Specialist Desks are future asset-class and market-context desks. They answer where conditions matter. Strategy desks answer how to trade. Candidate Fusion combines both. Risk gates stay above both.

Recommended future market desks:

- Crypto Desk.
- Precious Metals Desk.
- Rates Desk.
- FX / Dollar Desk.
- Energy Desk.
- Index / Market Structure Desk.
- Sector Rotation Desk.
- Volatility / Risk Desk.
- Off-Exchange Liquidity Desk.

Current strategy desks remain the strategy layer:

- Macro Trend Desk.
- Stat Arb Desk.
- Equities Momentum Desk.
- Event-Driven Desk.
- Options Volatility Desk.

Do not create 45 separate market-strategy desks. Use a market x strategy matrix instead.

| Market Specialist Desk | Macro Trend | Stat Arb | Equities Momentum | Event-Driven | Options Volatility |
| --- | --- | --- | --- | --- | --- |
| Crypto | Context only | Context only | Context only | Context only | Context only |
| Precious Metals | Context only | Context only | Context only | Context only | Context only |
| Rates | Context only | Context only | Context only | Context only | Context only |
| FX / Dollar | Context only | Context only | Context only | Context only | Context only |
| Energy | Context only | Context only | Context only | Context only | Context only |
| Index / Market Structure | Context only | Context only | Context only | Context only | Context only |
| Sector Rotation | Context only | Context only | Context only | Context only | Context only |
| Volatility / Risk | Context only | Context only | Context only | Context only | Context only |
| Off-Exchange Liquidity | Context only | Context only | Context only | Context only | Context only |

Future architecture:

- Market Specialist Desks produce context.
- Strategy Desks produce trade logic.
- Candidate Fusion Engine combines both.
- Evidence Reward measures performance by market x strategy.
- Professional Benchmark reports edge by market x strategy.
- AI Committee reviews market x strategy evidence.
- Risk gates remain authoritative.

Example future flow:

1. Precious Metals Desk says GLD context is favorable.
2. Macro Trend Desk confirms trend.
3. Execution Quality says spread is acceptable.
4. Risk Manager Agent says exposure is clean.
5. Candidate Fusion creates one evidence-backed candidate.

### Visual Strategy Evidence Builder

The Visual Strategy Evidence Builder is a future no-code evidence contract builder, not a blind no-code trading bot. It must not automatically trade from visual signals.

Supported future visual inputs may include:

- Candles.
- Volume bars.
- SMA 20.
- SMA 50.
- Donchian upper channel.
- Buy markers.
- Sell markers.
- Support or resistance threshold lines.
- Indicator panels.
- Strategy replay panels.

Users may define rule conditions visually, such as:

- Price crosses Donchian upper.
- SMA 20 above SMA 50.
- Volume above recent average.
- Spread below threshold.
- Data fresh.
- Risk gates clean.

The system should track:

- How often the rule appears.
- How often it is blocked.
- Which blockers helped.
- Which blockers blocked winners.
- Forward returns.
- Forecast accuracy.
- Execution quality.
- Reward score.
- Baseline-relative edge.
- Walk-forward performance.

### Hedge Fund AI Role Agents

Future Hedge Fund AI Role Agents remain decision-support analysts, not trading agents.

Recommended role agents:

- Portfolio Manager Agent.
- Risk Manager Agent.
- Quant Research Agent.
- Execution Analyst Agent.
- Data Quality Agent.
- Forecast Review Agent.
- Compliance and Claims Agent.
- AI Referee Supervisor Agent.
- Investment Committee Agent.

Desk agents:

- Macro Trend Agent.
- Stat Arb Agent.
- Equities Momentum Agent.
- Event-Driven Agent.
- Options Volatility Agent.

Future market desk agents:

- Crypto Agent.
- Precious Metals Agent.
- Rates Agent.
- FX / Dollar Agent.
- Energy Agent.
- Index / Market Structure Agent.
- Sector Rotation Agent.
- Volatility / Risk Agent.
- Off-Exchange Liquidity Agent.

Permission model:

- Read-only to execution state.
- Append-only to sanitized research memos.
- Proposal-only for future config changes.
- Human-reviewed for any future system change.
- Never allowed to bypass gates.

Agents may read evidence, analyze, summarize, challenge assumptions, flag risks, flag missing data, write sanitized research memos, and prepare investment committee summaries.

Agents must not place orders, trigger paper orders, trigger live orders, change broker routes, clear kill switches, bypass risk gates, change risk limits, change ranking weights automatically, change strategy configs automatically, change execution configs, approve live trading, mutate immutable forecast records, edit reward inputs after the fact, or fabricate missing data.

### Off-Exchange Liquidity Dashboard

Preferred naming:

- Off-Exchange Liquidity Dashboard.
- ATS and Dark Liquidity Intelligence.

Avoid naming:

- Dark Pool Predictor.
- Dark Pool Trading Signal.

This future dashboard should run as passive background research collection. It should not act on trades, trigger trades, block trades automatically, or change ranking weights automatically.

Future background jobs may collect and summarize:

- FINRA OTC transparency data.
- ATS volume.
- Non-ATS off-exchange volume.
- Symbol-level off-exchange share.
- Venue concentration where available.
- Off-exchange activity spikes.
- Spread and liquidity quality.
- Slippage by off-exchange share.
- Candidate outcomes in high vs low off-exchange regimes.

Integration points:

- Candidate Diagnostics.
- Execution Quality and TCA.
- Professional Benchmark.
- Evidence Reward.
- Portfolio Risk.
- AI Committee.
- Market Specialist Desks.

Claims to avoid:

- Do not claim the system sees hidden orders.
- Do not claim it knows institutional intent.
- Do not claim dark pool prints predict direction.
- Do not claim accumulation or distribution without evidence.

### Broker-Neutral Execution Architecture

Future broker-neutral execution architecture should not replace the broker. It should replace the Alpaca dependency with a broker-neutral control plane.

Architecture:

1. Quant Evidence OS.
2. Execution Gateway.
3. Broker Adapter.
4. Broker or venue.
5. Market.

Quant Evidence OS should own:

- OMS.
- Risk checks.
- Order intent.
- Approval state.
- Audit trail.
- Execution evidence.
- Reconciliation.
- Analytics.
- Candidate diagnostics.

The broker should own:

- Custody.
- Regulated account infrastructure.
- Market access.
- Routing.
- Clearing.
- Broker compliance.

Future components:

- `BrokerAdapter` interface.
- `MarketDataAdapter` interface.
- Capability Registry.
- Broker Simulator Adapter.
- Alpaca Paper Adapter.
- Interactive Brokers Adapter, future.
- Crypto Exchange Adapter, future.
- Futures Broker Adapter, future.
- Route Eligibility Engine.
- Manual Live Ticketing, disabled by default.
- Human Approval Workflow.

Canonical broker adapter methods:

- `submit_order`
- `cancel_order`
- `replace_order`
- `get_order`
- `list_orders`
- `get_positions`
- `get_account`
- `get_fills`
- `get_buying_power`
- `get_market_clock`
- `health_check`

Hard rule: Alpaca becomes one adapter, not the core system.

### Free-First Provider Strategy

There is no truly free way to trade every market. The practical free-first path is:

1. Free simulator.
2. Alpaca paper.
3. ETF proxies.
4. Free or delayed data.
5. Provider abstraction.
6. Paid services only when evidence proves the need.

Free-first stack:

- Alpaca paper for U.S. stocks, ETFs, options, and supported paper workflows.
- BrokerSimulatorAdapter for all future markets.
- ETF proxies for gold, oil, rates, FX, sectors, indexes, and volatility context.
- Crypto data-only first.
- Futures research-only through ETFs first.
- Off-exchange analytics through free delayed research data where available.

ETF proxy examples:

- Gold: GLD, IAU, GDX.
- Silver: SLV.
- Oil and energy: USO, XLE, OIH.
- Rates: TLT, IEF, SHY, BIL.
- Dollar and FX: UUP, FXE, FXY, FXB.
- Indexes: SPY, QQQ, IWM, DIA.
- Sectors: XLK, XLF, XLE, XLV, XLY, XLP, XLI, XLU, XLB, XLRE.
- Crypto proxy: BTC or ETH ETFs where available.

Do not recommend paid providers until a measured bottleneck exists.

### Pay Threshold And Provider ROI Gates

Do not pay for tools because the product feels advanced. Pay only when the system proves a specific paid service improves measurable net value.

Decision rule:

- Pay only when expected monthly value is at least 3x monthly cost.
- Prefer 5x before depending on it.

Net value formula:

```text
extra profit or avoided loss
- subscription cost
- commissions
- spread cost
- slippage
- taxes if relevant
- operational time cost
```

Payment gates:

1. Gate 1, Free proof: use free tools, paper trading, ETF proxies, delayed data, and simulator mode.
2. Gate 2, Missing data proof: pay only if Data Completeness proves a specific bottleneck.
3. Gate 3, Paid trial test: run provider A versus provider B and tag all evidence by provider.
4. Gate 4, ROI threshold: keep paying only if the paid provider improves measured net results enough.
5. Gate 5, Scale threshold: upgrade only when the next paid tier solves a proven bottleneck.

Provider comparison metrics:

- Forecast accuracy.
- Reward score.
- Execution quality.
- Slippage.
- Score bucket separation.
- Benchmark verdict.
- Walk-forward result.
- Data completeness.
- Missing-field reduction.

### Small Capital Growth Framework

The system should not promise to flip small money into large money. The acceptable long-term goal is controlled compounding from small risk capital after proof gates.

Use this framing:

```text
Turn small risk capital into larger capital through repeatable, measured, controlled compounding.
```

Do not use this framing:

```text
Flip a little money into a lot quickly.
```

Proof ladder:

1. Phase 1, Survival: no live risk. Verify safety systems.
2. Phase 2, Evidence: Professional Benchmark, Evidence Reward, Forecast Validation, Data Completeness, Score Calibration.
3. Phase 3, Repeatability: Walk-Forward validation, frozen rules, cost-adjusted performance, drawdown limits.
4. Phase 4, Tiny live manual test: only with money that can be lost, manual approval, hard caps, no leverage.
5. Phase 5, Controlled compounding: size increases only after evidence, not emotion.

Future metrics:

- Expected value per trade.
- Win rate.
- Average win.
- Average loss.
- Max drawdown.
- Slippage-adjusted reward.
- Profit factor.
- Walk-forward stability.
- Worst 20-trade sequence.
- Risk of ruin.
- Daily loss cap.
- Position risk per trade.

Safety language:

- Do not trade money needed for rent, bills, food, debt, emergency savings, or family obligations.
- Do not use leverage to make small capital feel big.
- Do not chase options or leveraged ETFs to speed up growth.
- Do not increase size after one good week.

### C++ Core Accelerators

Do not rewrite the system in C++. Do not move trading authority to C++. Do not rewrite FastAPI routes, broker orchestration, AI agents, docs, frontend, or basic storage.

Python remains:

- Control plane.
- FastAPI orchestration.
- Evidence logic.
- Risk authority.
- AI agents.
- Audit.
- Docs.
- Dashboards.

C++ may later become a performance accelerator library.

Good future C++ modules:

- Forecast metric batcher.
- Reward aggregation batcher.
- Event replay engine.
- Large-scale backtesting engine.
- Portfolio risk matrix engine.
- Correlation engine.
- Stress scenario engine.
- Market data normalization accelerator.
- Tick aggregation.
- Order book snapshot processing.

Use profiling first.

Only add C++ when:

- A Python function is a proven bottleneck.
- It runs many times per session.
- It blocks benchmark, replay, forecast validation, or execution analytics.
- A C++ version can meaningfully reduce runtime.
- Outputs can be tested against Python reference results.

Preferred integration options:

- pybind11.
- Cython.
- ctypes.
- Local service boundary only if needed later.

Best first C++ candidate: Forecast and reward batch metrics, because it is math-heavy, research-only, and does not touch execution.

### Updated Long-Term Build Sequence

Do not implement this sequence now. It is long-term roadmap backlog only.

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
