# Technical Analysis Evidence Setup Research

This document records the updated technical-analysis requirements for Quant Evidence OS. It is a docs, research, and backlog classification document only. It does not implement detectors, add routes, add frontend pages, place orders, change broker routes, change risk gates, clear kill switches, enable live trading, grant AI order authority, or let analytics change ranking weights automatically.

Source basis: user-provided Technical Analysis Research Report on 2026-05-12. The source set cited inside that report was not independently re-verified in this implementation pass, so this document treats the report as backlog direction and proof-gate policy, not as production evidence.

## Decision Rule

Do not admit "technical analysis" as one broad feature. Admit only method families that can be specified as causal rules, measured against simple baselines, evaluated on executable prices, and tested walk-forward after costs.

The current priority is:

1. High-priority method families become evidence-only setup candidates.
2. Medium-priority method families stay research-only or confirmation-only until they beat simpler controls.
3. Low-priority or avoid method families stay out of setup admission unless future evidence clears unusually strict out-of-sample gates.

## High-Priority Evidence-Only Candidates

These are the only method families that should move into near-term setup documentation before implementation.

| Method family | Minimum method fields | Matched controls | Admission condition |
| --- | --- | --- | --- |
| Momentum and trend indicators | `indicator_name`, `fast_len`, `slow_len`, `signal_len`, `slope`, `cross_state`, `adx_value` | Buy and hold, simple time-series momentum, neighboring parameters | Beats matched baselines net of costs across walk-forward folds. |
| Volume indicators | `indicator_name`, `obv_slope`, `mfi_value`, `rvol_zscore`, `poc`, `value_area_low`, `value_area_high` | Price-only version, no-volume confirmation version | Adds incremental net value beyond price-only controls. |
| Support and resistance | `level_type`, `level_price`, `level_width`, `touch_count`, `rejection_score`, `flip_flag` | Random horizontal levels, prior-bar extrema | Beats naive level controls with causal touch logic. |
| Dynamic support and resistance | `level_source`, `length`, `anchor_ts`, `slope`, `distance_bps`, `reclaim_flag` | Static support/resistance, simple crossover | Adds value beyond raw trend or crossover baselines. |
| Breakout patterns | `pattern_type`, `boundary_upper`, `boundary_lower`, `breakout_close`, `breakout_strength`, `volume_zscore`, `retest_flag` | Donchian, prior high/low, random levels | Exceeds simpler breakout controls net of slippage. |
| Reversal patterns | `pattern_family`, `pivot_points`, `neckline_level`, `symmetry_score`, `prior_trend_len`, `breakout_confirmed` | Simple swing reversal, RSI reversal | Improves out-of-sample net outcomes with causal detection. |
| Market structure | `pivot_method`, `swing_len`, `last_hh`, `last_hl`, `last_lh`, `last_ll`, `structure_state` | Moving-average regime, rolling regression trend | Improves downstream setup selection without lookahead pivots. |
| Break of structure | `bos_type`, `broken_pivot_price`, `break_close_distance`, `pivot_age`, `displacement_score` | Plain swing break, Donchian continuation | Beats simpler continuation controls with frozen pivot logic. |

## Medium-Priority Research-Only Candidates

These methods can be useful, but they should not become primary setup families until the rule contracts, controls, and out-of-sample proof are stronger.

| Method family | Allowed current role | Required proof before promotion |
| --- | --- | --- |
| Trend lines | Research-only level context | Anchor algorithm is fully specified and beats horizontal-level controls. |
| Oscillators | Confirmation layer only | Trend-gated version beats ungated oscillator and z-score mean-reversion controls after turnover costs. |
| Divergence | Confirmation or conviction-reduction layer | Beats same-indicator thresholds without divergence and survives pivot-pair perturbation. |
| Candlestick patterns | Small preregistered research subset | Beats shape-only and prior-return baselines after multiple-testing controls. |
| Fibonacci | Level-confluence research only | Beats equal-spaced retracement grids and generic swing-percentile levels. |
| Fair value gap | Research-only imbalance/retest study | Beats generic impulse-retest and random-zone controls. |
| Supply/demand and orderblocks | Research-only zone context | Beats generic consolidation-zone retests and standard support/resistance. |
| Change of character | Research-only reversal context | Adds value beyond simple market-structure shift or moving-average reversal baselines. |

## Low-Priority Or Avoid

These methods should not enter setup admission in the near-term backlog.

| Method family | Current status | Main reason |
| --- | --- | --- |
| Heikin Ashi | Avoid as standalone setup | Synthetic candle prices can distort fill and outcome evidence. |
| Renko | Avoid as standalone setup | Brick prices can be synthetic and projection-sensitive; fills must map back to actual prices. |
| Harmonic patterns | Research-only at most | Sparse samples, ratio-fitting freedom, and high multiple-testing risk. |
| Elliott Wave | Avoid | Subjective wave counts and unstable alternate interpretations. |
| Gann fan and angles | Avoid | Hidden chart-scaling degrees of freedom and weak empirical support. |
| Moon phases and cycles | Avoid | Weak structural linkage to executable market microstructure. |

## Shared Evidence Contract

Every admitted setup family must extend the same core evidence contract:

| Field group | Required content |
| --- | --- |
| Identity | `setup_id`, `method_id`, `instrument`, `venue`, `session_model`, `timeframe` |
| Causal timing | `timestamp_event`, `bar_index`, `state`, `entry_rule_id`, `parameter_set` |
| Market context | `regime_label`, `price_features`, `volume_features`, `level_features`, `confirmation_flags` |
| Trade hypothesis | `direction`, `invalidation_level`, `target_logic`, `holding_horizon` |
| Execution realism | `execution_assumption`, `spread_bps`, `slippage_bps`, `commission_bps`, `fill_proxy` |
| Outcomes | `outcome_label`, `raw_return`, `net_return`, `mfe`, `mae` |
| Benchmarking | `benchmark_id`, `wf_fold_id`, `provenance_hash` |

Synthetic chart values may be stored as derived features, but they do not qualify as fill prices, outcome prices, or proof of executable edge.

## Method-Specific Setup Contracts

These contracts are admission checklists only. They do not implement detectors, add execution logic, add broker routes, change ranking weights, or approve trading. A method family can become an evidence setup candidate only when its fields, matched controls, cost assumptions, walk-forward folds, and provenance are present before outcome review.

| Method family | Required setup fields | Matched controls | Proof blocker until complete |
| --- | --- | --- | --- |
| Momentum and trend indicators | `indicator_name`, `fast_len`, `slow_len`, `signal_len`, `slope`, `cross_state`, `adx_value`, `trend_regime`, `parameter_set` | Buy and hold, simple time-series momentum, neighboring lengths, no-trend-filter version | No score-quality or edge language until after-cost walk-forward lift beats simpler momentum controls. |
| Volume indicators | `indicator_name`, `volume_window`, `obv_slope`, `mfi_value`, `rvol_zscore`, `poc`, `value_area_low`, `value_area_high`, `volume_confirmation_rule` | Price-only setup, no-volume confirmation version, average-volume threshold | No incremental-value language until volume features improve net outcomes beyond price-only controls. |
| Support and resistance | `level_type`, `level_price`, `level_width`, `touch_count`, `last_touch_at`, `rejection_score`, `flip_flag`, `expiry_rule` | Random horizontal levels, prior-bar extrema, round-number level set | No level-quality claim until causal levels beat naive level controls without lookahead. |
| Dynamic support and resistance | `level_source`, `length`, `anchor_ts`, `slope`, `distance_bps`, `reclaim_flag`, `break_flag`, `expiry_rule` | Static support/resistance, simple moving-average crossover, rolling regression trend | No dynamic-level claim until it adds value beyond raw trend and static level baselines. |
| Breakout patterns | `pattern_type`, `boundary_upper`, `boundary_lower`, `lookback_len`, `breakout_close`, `breakout_strength`, `volume_zscore`, `retest_flag` | Donchian channel, prior high/low breakout, random-level breakout | No breakout-edge language until frozen breakout rules beat simpler breakout controls after slippage. |
| Reversal patterns | `pattern_family`, `pivot_points`, `neckline_level`, `symmetry_score`, `prior_trend_len`, `breakout_confirmed`, `invalidation_level` | Simple swing reversal, RSI reversal, prior-trend mean reversion | No reversal-quality claim until causal pivots and invalidation survive out-of-sample costs. |
| Market structure | `pivot_method`, `swing_len`, `last_hh`, `last_hl`, `last_lh`, `last_ll`, `structure_state`, `confirmation_bar` | Moving-average regime, rolling regression trend, simple higher-high/lower-low count | No structure-quality claim until no-lookahead pivots improve setup selection across regimes. |
| Break of structure | `bos_type`, `broken_pivot_price`, `break_close_distance`, `pivot_age`, `displacement_score`, `confirmation_bar`, `failed_break_flag` | Plain swing break, Donchian continuation, prior high/low continuation | No BOS claim until frozen pivot logic beats simpler continuation controls after costs. |

Every method-specific row must keep these blocked claims until evidence is complete:

- `proven_alpha`
- `repeatability_claim`
- `automatic_ranking_change`
- `paper_to_live_readiness`
- `live_trading_readiness`

The safe next action for any incomplete method is to document the missing fields, controls, costs, folds, and provenance. The safe next action is not to add a detector, trade from the method, or tune ranking weights.

## Baseline Definition Requirements

These baseline definitions must exist before detector work starts. They are research contracts only; they do not implement signals, change ranking weights, submit orders, change broker routes, bypass risk gates, or approve trading.

| Method family | Required baseline definitions | Baseline evidence blocker |
| --- | --- | --- |
| Momentum and trend indicators | `buy_hold_same_window`, `simple_time_series_momentum`, `neighboring_parameter_momentum`, `no_trend_filter_variant` | No momentum-quality claim until every candidate compares against same-window buy-hold, simple momentum, and nearby-parameter baselines after costs. |
| Volume indicators | `price_only_setup`, `no_volume_confirmation_variant`, `average_volume_threshold`, `random_volume_threshold` | No volume-incremental claim until volume features beat price-only and simple-volume controls net of turnover and spread. |
| Support and resistance | `random_horizontal_level`, `prior_bar_extrema`, `round_number_level`, `touch_count_only_level` | No support/resistance claim until causal levels beat naive level baselines without lookahead and with expiry rules applied. |
| Dynamic support and resistance | `static_support_resistance`, `moving_average_crossover`, `rolling_regression_trend`, `distance_to_average_only` | No dynamic-level claim until dynamic anchors add value beyond static levels, simple trend, and distance-only controls. |
| Breakout patterns | `donchian_channel`, `prior_high_low_breakout`, `random_level_breakout`, `breakout_without_retest_filter` | No breakout-edge claim until the proposed breakout rule beats simpler breakout controls after slippage, failed-break handling, and retest assumptions. |
| Reversal patterns | `simple_swing_reversal`, `rsi_reversal`, `prior_trend_mean_reversion`, `random_pivot_reversal` | No reversal-quality claim until causal pivot rules beat simple reversal controls with invalidation and target logic fixed before outcomes. |
| Market structure | `moving_average_regime`, `rolling_regression_trend`, `simple_higher_high_lower_low_count`, `random_pivot_structure` | No market-structure claim until no-lookahead pivots improve setup selection beyond simpler regime and trend baselines. |
| Break of structure | `plain_swing_break`, `donchian_continuation`, `prior_high_low_continuation`, `failed_break_counterfactual` | No BOS claim until frozen pivot breaks beat continuation baselines and include failed-break counterfactuals after costs. |

Each baseline definition must record `baseline_id`, `universe`, `session_model`, `timeframe`, `lookback_window`, `entry_timestamp_rule`, `exit_or_horizon_rule`, `cost_model`, `wf_fold_id`, and `provenance_hash`. Missing baseline definitions keep the method research-only and block score-quality, edge, repeatability, paper-to-live, and live-trading language.

## Universal Acceptance Criteria

A method can be documented as an evidence setup only when all of these gates are satisfied:

1. Causal rule completeness: anchor logic, pivot confirmation, thresholds, invalidation, and expiry are explicit.
2. Executable pricing only: outcomes and fills use actual OHLC, tick, quote, or bid/ask-derived proxies.
3. Benchmark superiority: the method beats a matched family baseline, not just a no-signal baseline.
4. Walk-forward robustness: frozen rules remain net-positive across a majority of out-of-sample folds and at least two materially different regimes.
5. Cost survival: spread, slippage, commission, and harsher-friction sensitivity are applied before ranking.
6. Parameter stability: nearby parameter choices preserve the signal direction and do not reveal a razor-thin optimum.
7. Replication and documentation: dataset, code version, assumptions, and provenance hash are recorded before admission.

## Near-Term Docs Backlog

1. Add method-specific setup contracts for the eight high-priority families.
2. Keep baseline definitions for each family complete before detector work starts.
3. Add "research-only" and "avoid" labels to roadmap references so weak methods do not drift into active implementation.
4. Keep all technical-analysis method work behind Professional Benchmark, Data Completeness, Execution Quality, and Walk-Forward proof gates.
5. Do not count synthetic chart transforms as executable proof.

## Project Finish Tracker

This tracker is project-wide and must stay at the end of report outputs. It is not limited to the technical-analysis setup layer.

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

Safety boundary: tracker items are verification, proof, review, documentation, paper-operation, or deferred roadmap work only. They do not authorize live trading, AI order authority, broker-route changes, risk-gate bypass, kill-switch bypass, ranking-weight mutation, or expansion implementation. They do not authorize order submission or deferred expansion work without separate proof-first approval.
