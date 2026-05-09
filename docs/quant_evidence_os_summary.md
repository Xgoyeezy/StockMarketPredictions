# Quant Evidence OS Technical Summary

This repository implements Quant Evidence OS as a local trading-control-plane application. The backend is FastAPI, the frontend is React/Vite, and runtime evidence is persisted through local books and append-only artifacts under `runtime/` and `runtime-exports/`.

## Operating Model

The system is paper-first and control-plane-first:

1. Market data, scanner output, opportunity capture, AI review, risk gates, allocator state, order evidence, and outcomes are captured as evidence.
2. Candidate diagnostics and reports explain why a trade did or did not happen.
3. Alpaca paper execution remains the only unattended route.
4. Live-control surfaces may exist for productized workflows, but autonomous live submission remains disabled unless separate explicit controls pass.

## Core Runtime Components

- `backend/services/trade_automation_service.py` - multi-desk scan scheduling, candidate diagnostics, paper-cycle decision evidence, opportunity capture, market-session artifacts, and prediction-contract emission for future reward scoring.
- `backend/services/trading_safety_service.py` - account-level safety state, safety ledger, route checks, objective/loss locks, and market-ready reporting.
- `backend/services/alpaca_paper_readiness_service.py` - non-secret Alpaca paper readiness, reconciliation, duplicate-order checks, and paper-route proof.
- `backend/services/automation_ai_review_service.py` - AI evidence review, shadow verdicts, notes, and review history.
- `backend/services/evidence_reward_engine.py` - research-only prediction-contract reward analytics.
- `hft_system/` - supervised paper-only high-frequency runtime experiments and watchdog evidence.

## Evidence Reward Contract

Evidence Reward deliberately avoids rewarding vague pattern labels. A row contributes to reward averages only when it has a complete prediction contract:

- `prediction_created_at`
- `predicted_direction`
- `prediction_horizon_minutes`
- `predicted_target_pct`
- `invalidation_level`
- `confidence`
- `actual_forward_return`
- `baseline_forward_return`

Rows are classified as:

- `rewardable` - complete, pre-move, baseline-backed prediction.
- `incomplete` - visible evidence, but missing required prediction fields.
- `baseline_missing` - prediction fields exist, but no baseline outcome exists.
- `post_move` - prediction timestamp came after the measured outcome and is excluded.

Reward components are transparent and exposed per row. They include direction correctness, target hit, low adverse excursion, timing correctness, confidence calibration, invalidation hit penalty, missed-target penalty, high-confidence wrong-call penalty, and baseline underperformance penalty.

The canonical Evidence Reward v1 formula is:

```text
total_reward =
  forward_return_score
  + baseline_relative_score
  - drawdown_penalty
  - slippage_penalty
  - spread_penalty
  - risk_violation_penalty
  + blocker_correctness_bonus
  - missed_move_penalty
```

Every component is exposed separately. Missing required prediction fields make the row `rewardable: false`; the row remains visible with `missing_fields`, `reason`, and `warnings`, but it does not affect reward averages.

## Forecast Validation Contract

Forecast Validation treats each chart overlay as an immutable prediction contract, not a drawing. A forecast is rewardable only when it was timestamped before the move and includes:

- `prediction_id`
- `symbol`
- `prediction_created_at`
- `horizon_minutes`
- `forecast_series`
- `predicted_direction`
- `predicted_target_pct`
- `invalidation_level`
- `confidence`
- `source` or model identity

Validation data is stored and returned separately from the original forecast record. The service evaluates only market data after `prediction_created_at` and reports incomplete actual series as missing data instead of fabricating results.

Forecast Validation v1 reports direction accuracy, path MAE, path RMSE, timing error, max adverse excursion, volatility mismatch, confidence calibration, target hit, invalidation hit, and time to target. Its reward formula is:

```text
forecast_total_reward =
  direction_score
  + path_fit_score
  + timing_score
  - drawdown_penalty
  - volatility_mismatch_penalty
  - confidence_penalty
```

Visual similarity and vague labels are not rewarded. A label like `bullish chart` is incomplete evidence unless it is attached to a timestamped contract such as: `VWAP reclaim predicts +0.6% within 60 minutes, invalid below VWAP, confidence 0.72`.

## Evidence Sources

Evidence Reward and related reports reuse current artifacts instead of adding a new database:

- `runtime-exports/candidate-lifecycle/**/{tenant_slug}.jsonl`
- `runtime-exports/evidence-accelerator/**/{tenant_slug}.jsonl`
- `runtime-exports/simulation-evidence/**/{tenant_slug}.jsonl`
- `runtime-exports/market-days/**/market-day-report.json`
- local paper books via `read_open_trades()`, `read_closed_trades()`, and `read_pending_orders()`

Simulation evidence is counted separately and does not contaminate live-observed reward metrics.

## Customer And Operator Surfaces

- Market Watchdog: system liveness, worker state, desk scans, Alpaca paper readiness, reconciliation, kill switch, no-trade checkpoints, and next safe action.
- Candidate Diagnostics: per-candidate score, setup, blocker, AI verdict, routeability, and evidence state.
- Evidence Reward: prediction-contract reward analytics and incomplete-evidence warnings.
- Forecast Validation: forward-only prediction path evaluation.
- Execution Quality: slippage, spread, latency, fill quality, and reconciliation evidence.
- Audit Replay: traceability for decisions and operational events.
- Market-Day Reports: close-of-day proof including no-trade reasons, missed opportunities, safety events, and desk blockers.

## Public API Shape

The app keeps the `{ ok, data, meta }` response envelope. Relevant read/report APIs include:

- `GET /api/orgs/trade-automation/watchdog`
- `GET /api/orgs/trade-automation/market-session`
- `GET /api/orgs/trade-automation/safety-state`
- `GET /api/orgs/trade-automation/desks`
- `GET /api/orgs/trade-automation/candidate-diagnostics`
- `GET /api/orgs/trade-automation/no-trade-report`
- `GET /api/orgs/trade-automation/market-day-report`
- `GET /api/orgs/trade-automation/alpaca-paper-readiness`
- `GET /api/orgs/trade-automation/evidence-reward/summary`
- `GET /api/evidence-reward/summary`
- `GET /api/evidence-reward/candidates`
- `GET /api/evidence-reward/blockers`
- `GET /api/evidence-reward/engines`
- `GET /api/evidence-reward/setups`
- `GET /api/evidence-reward/ai`
- `GET /api/evidence-reward/regimes`
- `GET /api/forecast-validation/summary`
- `GET /api/forecast-validation/predictions`
- `GET /api/forecast-validation/models`
- `GET /api/forecast-validation/regimes`

Evidence Reward and Forecast Validation responses keep the `{ ok, data, meta }` envelope and include research-only safety notes inside `data`.

## Non-Negotiable Invariants

- No autonomous live-money order submission.
- No AI order authority.
- No risk-gate bypass.
- No automatic kill-switch clearing.
- No broker-route loosening.
- No simulated evidence counted as live-observed evidence.
- No vague chart label rewarded as edge.
- Alpaca paper remains the only unattended execution route.

## Validation Commands

```powershell
python -m compileall -q backend tests scripts
python -m unittest tests.test_evidence_reward_engine_service tests.test_forecast_validation_engine tests.test_forecast_validation_api tests.test_api_route_health
python -m unittest discover -s tests -p "test_automation_*.py"
cd frontend
npm.cmd run build
```

## Professional Planning Docs

The current strategy and benchmark planning documents live in `docs/`:

- `CURRENT_STATE_AND_PROFESSIONAL_RATINGS.md`
- `TEN_OUT_OF_TEN_ROADMAP.md`
- `PROFESSIONAL_BENCHMARK_SUITE.md`
- `PRODUCT_POSITIONING_AND_BUYER_CATEGORIES.md`
