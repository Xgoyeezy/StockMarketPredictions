# Professional Benchmark Suite

The Professional Benchmark Suite is the read-only proof layer for Quant Evidence OS. It answers one question: did the system beat simple baselines after realistic costs, using forward-only evidence?

Professional Benchmark Suite v1 is implemented as analytics only. It does not change trading behavior, order routing, risk gates, ranking weights, AI authority, kill switches, paper execution, or live-trading state.

## Role In 10/10 Upgrade Plan

Professional Benchmark is one of the primary proof gates in `docs/TEN_OUT_OF_TEN_CATEGORY_UPGRADE_MASTER_PLAN.md` and `docs/TEN_OUT_OF_TEN_PROOF_GATES.md`. It is also referenced by `docs/TEN_OUT_OF_TEN_ACCEPTANCE_CHECKLIST.md` and `docs/TEN_OUT_OF_TEN_30_60_90_DAY_PLAN.md` as the required bridge between raw evidence and any cautious edge claim.

The benchmark layer must keep the category-rating safety language intact: ratings are current estimated readiness scores, ratings are not proof of alpha, ratings are not investor performance claims, benchmark proof is required before claiming edge, walk-forward proof is required before claiming repeatability, paper-first safety remains the active execution boundary, reward and forecast analytics are research-only, AI has no order authority, risk gates remain authoritative, broker routes remain unchanged, live-money autonomy is not enabled, and promotion status is research metadata unless separately approved by a future explicit governance framework.

## Purpose

The suite should prove or disprove:

- Whether forecasts were directionally correct.
- Whether prediction paths matched actual paths.
- Whether higher-ranked candidates beat lower-ranked candidates.
- Whether setup types had positive reward after costs.
- Whether engines worked in specific regimes.
- Whether blockers prevented losses or blocked winners.
- Whether AI verdicts improved review quality.
- Whether missed moves were recoverable.
- Whether execution quality supported the forecast.
- Whether results held out of sample.

## Required Baselines

Each benchmark report must compare Quant Evidence OS against simple baselines:

- SPY drift.
- QQQ drift.
- Sector ETF drift.
- Random candidate from the same universe and time window.
- Simple momentum.
- Simple mean reversion.
- Simple VWAP reclaim.
- Opening range breakout.
- Previous close drift.

Baseline rules:

- Baselines must use the same market session, universe, tradability filter, data availability, and cost assumptions as the system.
- Baselines must be timestamped and forward-only.
- Baselines must not receive hindsight entry, exit, or symbol selection.
- Baselines must be reported even when the system underperforms them.

## Implemented V1 Endpoints

The v1 API endpoints are mounted under the existing API prefix:

- `GET /api/professional-benchmark/summary`
- `GET /api/professional-benchmark/baselines`
- `GET /api/professional-benchmark/score-buckets`
- `GET /api/professional-benchmark/blockers`
- `GET /api/professional-benchmark/ai`
- `GET /api/professional-benchmark/forecast`
- `GET /api/professional-benchmark/execution`

Each response includes:

- `status`
- `generated_at`
- `research_only: true`
- `summary`
- `records`
- `aggregations`
- `baselines`
- `proof_summary`
- `benchmark_hardening_plan`
- `warnings`
- `missing_fields`
- `safety_notes`

The customer UI route is `/professional-benchmark`.

## Benchmark Proof Gate

The summary includes `proof_summary`, a human-review gate for the roadmap proof items that must pass before any cautious benchmark edge language is considered:

- rewardable sample size
- explicit forward-only baseline availability
- baseline-relative edge
- score bucket lift
- after-cost reward
- data quality floor

`proof_summary.proof_ready` means the benchmark evidence is ready for human research review only. It is not proof of alpha, not an investor performance claim, not repeatability proof, not institutional readiness, and not live-trading readiness. Walk-forward proof remains required before any repeatability claim.

Missing proof requirements create manual safe next actions. They do not place orders, change broker routes, bypass risk gates, clear kill switches, alter ranking weights, or approve live trading.

## Benchmark Hardening Plan

The summary also includes `benchmark_hardening_plan`, a proof-first work queue for the benchmark layer. It turns missing proof into explicit claim boundaries instead of stronger marketing language.

The hardening plan tracks:

- rewardable sample and data quality
- same-window explicit baselines
- baseline-relative edge
- score bucket lift
- after-cost reward
- out-of-sample split and frozen versions

Each item reports priority, status, linked proof keys, missing fields, blocked claims, a safe next action, and a `done_when` condition. The plan also returns `claim_permissions` so the UI can show that public alpha, repeatability, institutional readiness, live-trading readiness, and guaranteed-return claims remain blocked.

Hardening plan items are internal research gates only. They do not fabricate outcomes or baselines, authorize orders, change broker routes, bypass risk gates, clear kill switches, alter ranking weights, or approve live trading.

## Verdict Rules

The v1 verdict is deliberately conservative:

- `insufficient_evidence`: no evidence exists or fewer than five rewardable rows exist.
- `data_quality_too_weak`: more than half of candidate rows are missing rewardable outcome fields.
- `edge_detected`: average reward is positive, explicit baseline-relative edge is at least `0.10` percentage points, and high score buckets beat low score buckets.
- `weak_edge_detected`: average reward or explicit baseline-relative edge is positive, but score separation or edge magnitude is not strong enough.
- `no_edge_detected`: rewardable rows do not currently beat simple baselines after observed costs.

These verdicts are research labels only. They cannot change ranking weights, submit orders, clear gates, or alter broker routes.

## Required Performance Metrics

Core metrics:

- Hit rate.
- Expected value.
- Sharpe.
- Sortino.
- Max drawdown.
- Profit factor.
- Average reward.
- Median reward.
- Reward dispersion.
- Turnover.
- Slippage.
- Spread cost.
- Capacity estimate.
- Confidence calibration.
- Regime stability.

Implemented v1 formulas:

```text
hit_rate = positive_actual_forward_return_count / observed_actual_forward_return_count
expected_value = average(actual_forward_return)
average_reward = average(total_reward)
median_reward = median(total_reward)
reward_dispersion = population_standard_deviation(total_reward)
profit_factor = sum(positive_rewards) / abs(sum(negative_rewards))
slippage_adjusted_reward = average(total_reward - abs(slippage_bps) / 100)
spread_cost = average(spread_bps / 100)
score_bucket_lift = average_reward(80+ score buckets) - average_reward(0-59 score buckets)
baseline_relative_edge = average(actual_forward_return) - average(explicit_baseline_forward_return)
```

Max drawdown v1 is computed from the cumulative reward sequence when rewardable rows exist. It is a reward-sequence proxy until a full portfolio equity curve is available.

Decision quality metrics:

- Reward by setup.
- Reward by engine.
- Reward by regime.
- Reward by score bucket.
- Blocker value.
- False block rate.
- Missed winner rate.
- AI approve reward.
- AI reject reward.
- AI false-positive rate.
- AI false-negative rate.

Execution metrics:

- Expected fill vs paper fill.
- Spread at decision time.
- Slippage bps.
- Time to submit.
- Time to fill.
- Partial fill rate.
- Rejection rate.
- Alpha decay after signal.

## Benchmark Sections

### Forecast Accuracy

Required outputs:

- Direction accuracy.
- Path MAE.
- Path RMSE.
- Timing error.
- Max adverse excursion.
- Volatility mismatch.
- Confidence calibration.
- Target hit.
- Invalidation hit.
- Time to target.

Pass criteria:

- Forecasts beat baseline direction accuracy after costs and have acceptable calibration.
- Correct forecasts do not require unacceptable adverse excursion before target.

Fail criteria:

- Forecasts are vague, post-move, missing baseline outcome, or only visually similar.
- Direction accuracy beats baseline but drawdown, slippage, or timing makes the forecast untradable.

### Reward By Setup

Required outputs:

- Average reward by setup type.
- Median reward by setup type.
- Reward dispersion.
- Consistency score.
- Number of rewardable contracts.
- Missing-field count.

Pass criteria:

- A setup shows positive baseline-adjusted reward across enough rewardable contracts and multiple sessions.

Fail criteria:

- Setup labels are not tied to timestamped prediction contracts.
- Positive reward is concentrated in one day, one symbol, or one regime without enough support.

### Reward By Engine

Required outputs:

- Average reward.
- Win rate.
- Reward by regime.
- Reward by score bucket.
- Cost-adjusted reward.
- Capacity estimate.

Pass criteria:

- Engine reward survives costs, regime splits, and out-of-sample tests.

Fail criteria:

- Engine performs only before costs or only in one hindsight-selected segment.

### Reward By Regime

Required outputs:

- Trend day reward.
- Range day reward.
- Volatility expansion reward.
- Low-volatility compression reward.
- Risk-on/risk-off reward.
- Late-day continuation/reversal reward.

Pass criteria:

- Regime-specific performance is stable enough to justify manual playbook review.

Fail criteria:

- Regime labels are missing, post-facto, or not timestamped.

### Score Bucket Separation

Required outputs:

- Reward by score bucket.
- Forward return by score bucket.
- Cost-adjusted outcome by score bucket.
- Monotonicity check.
- Confidence interval or sample-size warning.

Pass criteria:

- Higher score buckets produce better reward than lower buckets after costs and across out-of-sample windows.

Fail criteria:

- Scores do not separate outcomes or only separate outcomes in sample.

### Blocker Value

Required outputs:

- Times seen.
- Times blocked.
- Average forward return after block.
- Estimated blocker value.
- False block rate.
- Confidence bucket.

Formula:

```text
estimated_blocker_value = -average_forward_return_after_block
```

Interpretation:

- Positive value means the blocker likely avoided losses.
- Negative value means the blocker may have blocked winners.

Pass criteria:

- Strict blockers such as kill switch, stale data, route block, reconciliation block, and loss lock prevent harmful states and remain strict.
- Softer blockers show measurable loss avoidance or are flagged for review.

Fail criteria:

- A blocker repeatedly blocks forward winners without enough protective value.

### AI Verdict Accuracy

Required outputs:

- Reward after AI approve.
- Reward after AI reject.
- False-positive rate.
- False-negative rate.
- Evidence incomplete rate.
- Latency and timeout rate.

Pass criteria:

- AI improves review quality or missing-evidence detection without order authority.

Fail criteria:

- AI approvals correlate with poor outcomes or AI rejections repeatedly block winners without clear evidence.

Safety rule:

- AI verdicts never place orders, clear gates, change routes, change risk limits, mutate ranking weights, or approve live trading.

### Missed Move Recovery

Required outputs:

- Missed move magnitude.
- Missed move by blocker.
- Missed move by setup.
- Missed move by engine.
- Would-catch-now replay result.
- Correct block vs bad miss.

Pass criteria:

- The system identifies repeatable missed-edge causes that can be reviewed manually.

Fail criteria:

- Missed moves are counted without forward-known entry rules, cost assumptions, or risk context.

### Execution Quality

Required outputs:

- Slippage-adjusted reward.
- Spread-adjusted reward.
- Fill probability.
- Time-to-fill.
- Paper vs expected fill.
- Alpha decay.
- Partial fill analysis.

Pass criteria:

- Tradable forecasts stay positive after realistic spread, slippage, and delay assumptions.

Fail criteria:

- Forecasts are correct but untradeable after execution costs.

## Walk-Forward Testing Design

Minimum design:

1. Split evidence into chronological train, validation, and test windows.
2. Freeze strategy settings, reward formula versions, forecast model versions, data filters, and cost assumptions before the test window.
3. Run the system and baselines on the same out-of-sample period.
4. Report all outcomes, including underperformance.
5. Repeat across multiple market regimes.

Frozen-parameter rule:

- No parameter may be changed after seeing the test-window outcome.
- Any parameter change creates a new experiment version.
- The old experiment remains visible for audit.

No-lookahead rule:

- Every feature, forecast, ranking, blocker, AI verdict, and decision must have a timestamp.
- Evaluation may only use market data after the decision timestamp.
- Current price must never substitute for missing forward data.

## Cost Model Requirements

The benchmark must include:

- Spread cost.
- Slippage estimate.
- Time-to-fill delay.
- Partial fill assumptions.
- Rejection assumptions.
- Turnover.
- Capacity estimate.
- Borrow or shorting cost only if future short-sale support is explicitly implemented.

Cost rules:

- Use conservative defaults when broker evidence is missing.
- Mark missing cost fields as missing instead of fabricating precision.
- Show pre-cost and post-cost results.

## Data Requirements And Missing Data

The suite reuses current research outputs instead of introducing a new database:

- Evidence Reward candidate rows.
- Forecast Validation records.
- Existing execution-cost fields when available.
- Explicit baseline forward-return fields when available.

No baseline is fabricated. If a baseline is missing, the response marks that baseline as:

```json
{
  "available": false,
  "missing_fields": ["required_field"],
  "reason": "No explicit baseline evidence was found."
}
```

Simulation evidence remains separate from real-time market-observed evidence. It must not be counted as live-observed benchmark proof.

## Baseline Definitions

V1 recognizes these explicit baseline fields:

- SPY drift: `spy_forward_return`, `spy_drift`, or `baseline_spy_return`.
- QQQ drift: `qqq_forward_return`, `qqq_drift`, or `baseline_qqq_return`.
- Sector ETF drift: `sector_etf_forward_return`, `sector_baseline_forward_return`, or `baseline_sector_return`.
- Random candidate: `random_candidate_forward_return` or `baseline_forward_return`.
- Simple momentum: `simple_momentum_forward_return` or `momentum_baseline_forward_return`.
- Simple mean reversion: `simple_mean_reversion_forward_return` or `mean_reversion_baseline_forward_return`.
- Simple VWAP reclaim: `simple_vwap_reclaim_forward_return` or `vwap_reclaim_baseline_forward_return`.
- Opening range breakout: `opening_range_breakout_forward_return` or `orb_baseline_forward_return`.

If these fields are absent, the baseline remains unavailable. The suite does not substitute current price, hindsight labels, or simulation-only outcomes.

## Report Format

Each report should include:

- Report ID and generated timestamp.
- Data range and market sessions.
- Universe and filters.
- Evidence source list.
- Strategy/config version.
- Reward formula version.
- Forecast model version.
- Cost model version.
- Baselines used.
- Metrics table.
- Segment table by setup, engine, regime, score bucket, and blocker.
- Best and worst segments.
- Missing data warnings.
- Safety notes.
- Pass/fail summary.
- Manual-review recommendations.

## UI

The `/professional-benchmark` page shows:

- Research-only label.
- Overall benchmark verdict.
- Baseline comparison.
- Score bucket separation.
- Reward by setup.
- Reward by engine.
- Reward by regime.
- Blocker value.
- AI verdict quality.
- Forecast accuracy.
- Execution-adjusted reward.
- Missing data warnings.
- Sample-size and out-of-sample status.

The page must not imply guaranteed returns, live trading, automatic ranking changes, AI trading authority, or benchmark-driven execution.

## Pass And Fail Criteria

A system segment can be marked as passing only when:

- It has enough rewardable, timestamped, pre-move contracts.
- It beats relevant baselines after costs.
- It is stable out of sample.
- It survives regime segmentation.
- It does not rely on simulation evidence as live-observed evidence.
- It does not require bypassing hard gates.

A system segment must fail or remain inconclusive when:

- Required fields are missing.
- Evidence is post-move.
- Baselines are missing.
- Sample size is too small.
- Costs erase the edge.
- Results are isolated to one symbol, one day, or hindsight-selected period.
- Data lineage is not clear.

## Safety Notes

- Benchmark reports are research outputs.
- Benchmark reports do not place orders.
- Benchmark reports do not change broker routes.
- Benchmark reports do not bypass risk gates.
- Benchmark reports do not clear kill switches.
- Benchmark reports do not grant AI order authority.
- Benchmark reports do not mutate ranking weights automatically.
- Simulation evidence remains separate from real-time market-observed evidence.

## Tests

Run the focused benchmark tests:

```powershell
python -m unittest tests.test_professional_benchmark_suite_service tests.test_api_route_health
```

Run the broader validation set:

```powershell
python -m compileall -q backend tests scripts
python -m unittest tests.test_professional_benchmark_suite_service tests.test_api_route_health
python -m unittest discover -s tests -p "test_automation_*.py"
cd frontend
npm.cmd run build
```

## Limitations

- V1 can return `insufficient_evidence` by design.
- Out-of-sample stability is marked unavailable until experiment splits and frozen-parameter versions are present.
- Max drawdown is a reward-sequence proxy until a full portfolio equity curve is connected.
- Baselines require explicit forward-only baseline fields.
- Benchmark conclusions are internal research estimates, not investment advice or proof of professional-grade alpha.

## Candidate Outcome Source

Professional Benchmark v1 consumes candidate forward outcomes through Evidence Reward. The source evidence is `runtime-exports/candidate-outcomes/<date>/<tenant>.jsonl`, linked back to immutable candidate lifecycle rows by `candidate_lifecycle_id`.

Candidate Outcome and Baseline Stamping supplies `actual_forward_return`, matched baseline returns, score bucket inputs, and paper-route execution cost fields. Missing outcome, baseline, execution-cost, or regime data keeps benchmark sections unavailable instead of fabricating proof.

Benchmark outputs remain research-only. They do not place trades, change broker routes, bypass risk gates, clear kill switches, or automatically change ranking weights.

## Future Market x Strategy Benchmark

Market x Strategy Benchmark is a future roadmap item only. It should measure outcomes by market context and strategy logic without creating 45 combined market-strategy desks.

Future benchmark dimensions may include:

- Market Specialist Desk context, such as Precious Metals, Rates, FX / Dollar, Energy, Volatility / Risk, or Off-Exchange Liquidity.
- Current Strategy Desk logic, such as Macro Trend, Stat Arb, Equities Momentum, Event-Driven, or Options Volatility.
- Candidate Fusion evidence for the combined market context and strategy logic.
- Baseline-relative edge by market x strategy.
- Walk-forward performance by market x strategy.
- Execution-adjusted reward by market x strategy.

This benchmark remains research-only. It cannot place orders, change broker routes, bypass risk gates, clear kill switches, change ranking weights automatically, or merge simulation evidence into real-time market-observed evidence.
