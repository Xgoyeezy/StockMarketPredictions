# Portfolio Risk Intelligence v1

## Purpose

Portfolio Risk Intelligence moves Quant Evidence OS from trade-level safety visibility to portfolio-level risk visibility. It is a read-only analytics layer for Alpaca paper-route evidence, open paper positions, pending paper intent, and available candidate evidence.

It does not enforce risk limits. It does not loosen risk limits. It does not place, block, cancel, or route orders.

## Paper-Only Boundary

The service reports:

- `research_only: true`
- `paper_only: true`
- `paper_route_only: true`
- `can_submit_orders: false`
- `can_submit_live_orders: false`
- `mutation: "none"`

Portfolio Risk Intelligence is separate from existing safety gates and broker routing. Risk gates remain authoritative, and broker routes remain unchanged.

## Metrics

- `gross_exposure`: Sum of absolute paper exposure.
- `net_exposure`: Signed exposure after long and inverse/short proxy direction.
- `long_exposure`: Gross long exposure.
- `short_or_proxy_exposure`: Gross exposure from short-like or inverse proxy records such as `SH`, `PSQ`, `DOG`, `RWM`, and `VXX`.
- `sector_exposure`: Exposure grouped by explicit sector or known symbol/ETF mapping.
- `engine_exposure`: Exposure grouped by engine or desk key.
- `setup_exposure`: Exposure grouped by setup type.
- `strategy_exposure`: Exposure grouped by strategy key.
- `symbol_concentration`: Largest symbol exposure divided by gross exposure.
- `sector_concentration`: Largest sector exposure divided by gross exposure.
- `correlation_heat`: Concentration score based on configured/default correlation buckets.
- `liquidity_exposure`: Exposure with weak liquidity score, low average dollar volume, or wide spread evidence.
- `beta_to_SPY` and `beta_to_QQQ`: Exposure-weighted beta when beta fields exist.
- `drawdown_state`: Visibility label based on available drawdown or floating P&L evidence.
- `daily_risk_budget_usage`: Open risk estimate divided by available daily risk budget evidence or the paper default.
- `open_heat`: Gross exposure divided by account-size evidence.
- `regime_exposure`: Exposure grouped by market regime label.
- `forecast_confidence_exposure`: Exposure grouped by forecast confidence bucket.

## Stress Scenarios

The v1 stress tests are simple diagnostics:

- `market_down_2_percent`
- `market_down_5_percent`
- `volatility_expansion`
- `liquidity_deterioration`
- `sector_rotation`
- `single_name_gap_down`
- `failed_breakout_cluster`
- `data_outage`
- `broker_outage`

These scenarios are analytics only. They do not alter risk gates, broker routes, kill switches, ranking weights, or order behavior.

## Data Requirements

Best results need:

- symbol
- paper route or paper source
- notional, position cost, market value, or quantity plus price
- side or inverse proxy symbol
- sector
- engine
- setup type
- strategy
- regime
- beta to SPY and QQQ
- liquidity score or average dollar volume
- spread evidence
- forecast confidence
- max risk dollars or daily risk budget
- drawdown or unrealized P&L

Missing fields are reported in `missing_fields` and surfaced in the UI. Missing data is not fabricated.

## APIs

- `GET /api/portfolio-risk/summary`
- `GET /api/portfolio-risk/exposures`
- `GET /api/portfolio-risk/concentration`
- `GET /api/portfolio-risk/correlation`
- `GET /api/portfolio-risk/stress-tests`
- `GET /api/portfolio-risk/regimes`

Every endpoint returns the standard `{ ok, data, meta }` envelope. The `data` payload includes `status`, `generated_at`, `research_only`, `paper_only`, `summary`, `records`, `aggregations`, `warnings`, `missing_fields`, and `safety_notes`.

## UI Route

Open:

- `/portfolio-risk`

The page shows the paper-only/research-only boundary, gross and net exposure, sector/engine/setup/regime exposure, concentration, correlation heat, liquidity warnings, drawdown state, risk budget usage, stress scenarios, and missing data warnings.

## Benchmark Support

Portfolio Risk Intelligence supports the Professional Benchmark Suite by making portfolio concentration, regime crowding, liquidity drag, beta exposure, and stress risk visible before evaluating whether an apparent edge is actually usable.

## Test Commands

```powershell
python -m unittest tests.test_portfolio_risk_intelligence tests.test_api_route_health
python -m compileall -q backend tests scripts
npm.cmd run build
```

Run the frontend build from `frontend/`.

## Limitations

- Correlation heat is bucket-based in v1, not a full covariance matrix.
- Beta is reported only when beta fields exist.
- Stress tests use simple transparent shocks, not a full factor model.
- Portfolio Risk Intelligence does not enforce or change risk limits.
- Live-money trading is not enabled by this layer.

## Candidate Outcome Source

Portfolio Risk Intelligence can use stamped candidate outcomes for research visibility into open heat, regime exposure, confidence exposure, and execution-adjusted outcome context. The source remains append-only candidate outcome evidence linked by `candidate_lifecycle_id`.

Portfolio Risk Intelligence is risk visibility only. It does not place or block orders, change risk limits, bypass risk gates, change broker routes, or automatically change ranking weights.
