# Score Calibration And Feature Attribution

## Purpose

Score Calibration and Feature Attribution v1 measures whether candidate scores and score components predict reward, forecast quality, and execution-adjusted outcomes. It is analytics only. It does not change ranking weights, broker routes, risk gates, order logic, live settings, or AI authority.

## Data Sources

The v1 service reads the existing Professional Benchmark and Evidence Reward records. Simulation evidence remains separate and does not become real-time market-observed evidence.

Required fields for full calibration:

- `score`, `ranking_score`, `setup_score`, or `opportunity_score`
- `total_reward`
- `actual_forward_return`
- `baseline_forward_return`

Optional fields improve attribution:

- `setup_type`
- `engine`
- `regime`
- `ai_verdict`
- `blockers`
- `slippage_bps`
- `spread_bps`
- `forecast_accuracy` or `direction_accuracy`
- `component_scores`
- `ranking_components`
- `features`

Missing fields are reported instead of inferred.

## Score Bucket Methodology

Scores are bucketed into transparent ranges:

- `0_20`
- `20_40`
- `40_60`
- `60_80`
- `80_100`

If the source score scale appears to be 0-1, scores are multiplied by 100 for bucket analysis. If scores fall outside 0-100, they are clamped for bucket assignment and the report documents the detected scale.

Each bucket reports:

- candidate count
- rewardable count
- average reward
- median reward
- hit rate
- baseline-relative edge
- forecast accuracy when available
- execution-adjusted reward when available
- missing-data rate

`bucket_lift` is the average reward of the `80_100` bucket minus the average reward of the lower `0_20` and `20_40` buckets.

`monotonicity_score` is the share of adjacent bucket comparisons where the higher bucket has equal or better average reward.

## Feature Attribution Methodology

v1 uses transparent methods only:

- grouped averages
- difference in means
- univariate lift
- segment lift
- simple correlation

It does not use black-box ML, train production models, or tune live parameters.

Feature attribution records include:

- times seen
- average reward when present
- average reward when absent
- lift
- false-positive rate
- false-negative rate
- regime dependency
- confidence bucket
- warnings

Feature sources include setup type, engine, regime, AI verdict, blockers, and positive numeric score components.

## False Positive And False Negative Drivers

False-positive drivers are features present in high-score records that later produced negative reward or negative forward return.

False-negative drivers are features present in low-score or blocked records that later produced positive reward or a forward winner.

These are manual review signals only.

## Safe Recommendation Rules

Valid recommendations:

- review weight of feature X
- feature Y has weak lift
- setup Z works only in a specific regime
- score bucket separation is poor
- score bucket separation is strong but needs walk-forward validation

Invalid recommendations:

- automatically change a weight
- increase live size
- bypass a blocker
- place an order
- change a broker route
- clear a kill switch
- alter risk limits

## API Endpoints

All paths are under the configured API prefix, usually `/api`.

- `GET /api/score-calibration/summary`
- `GET /api/score-calibration/buckets`
- `GET /api/score-calibration/features`
- `GET /api/score-calibration/regimes`
- `GET /api/score-calibration/recommendations`

Every response includes:

- `research_only: true`
- `can_submit_orders: false`
- `can_submit_live_orders: false`
- `mutation: "none"`
- safety notes stating that ranking weights and trading behavior are unchanged.

## UI Route

- `/score-calibration`

The page shows score bucket separation, monotonicity, best and worst features, false-positive drivers, false-negative drivers, feature lift by regime, safe recommendations, warnings, and missing data.

## Research-Only Boundary

Score Calibration v1 cannot:

- enable live trading
- submit paper or live orders
- change broker routes
- bypass risk gates
- clear kill switches
- grant AI order authority
- mutate ranking weights automatically
- merge simulation evidence into real-time market-observed evidence

## Test Commands

```powershell
python -m compileall -q backend tests scripts
python -m unittest tests.test_score_calibration_attribution tests.test_api_route_health
python -m unittest tests.test_professional_benchmark_suite_service tests.test_evidence_reward_engine_service tests.test_score_calibration_attribution tests.test_api_route_health
npm.cmd run build
```

Run the frontend build from:

```powershell
cd frontend
```

## Limitations

- v1 is descriptive analytics, not causal proof.
- Small feature samples are marked as low confidence.
- Missing reward, baseline, forecast, or execution fields reduce attribution quality.
- Strong score separation still requires walk-forward validation before any human considers changing research configuration.

## Candidate Outcome Source

Score Calibration consumes stamped candidate outcomes through Professional Benchmark and Evidence Reward. The stamped outcome records provide forward returns, primary baselines, score buckets, and execution-cost fields linked by `candidate_lifecycle_id`.

Calibration recommendations are research-only. They may say to review a feature or score component, but they must not mutate ranking weights, change risk settings, bypass blockers, change broker routes, or trigger trades automatically.
