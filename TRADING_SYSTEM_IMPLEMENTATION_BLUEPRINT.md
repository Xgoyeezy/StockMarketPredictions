# Trading System Implementation Blueprint

This blueprint turns the recent trading research into a repo-specific implementation plan for:
- [C:\Users\marcc\PycharmProjects\StockMarketPredictions\ALGORITHMIC_TRADING_ROADMAP.md](</C:/Users/marcc/PycharmProjects/StockMarketPredictions/ALGORITHMIC_TRADING_ROADMAP.md>)
- [C:\Users\marcc\PycharmProjects\StockMarketPredictions\COMING_UPDATES_RESEARCH_ACTION_PLAN.md](</C:/Users/marcc/PycharmProjects/StockMarketPredictions/COMING_UPDATES_RESEARCH_ACTION_PLAN.md>)
- [C:\Users\marcc\PycharmProjects\StockMarketPredictions\MARKET_STRUCTURE_PREDICTION_ROADMAP.md](</C:/Users/marcc/PycharmProjects/StockMarketPredictions/MARKET_STRUCTURE_PREDICTION_ROADMAP.md>)
- [C:\Users\marcc\PycharmProjects\StockMarketPredictions\REAL_MONEY_EXECUTION_ROADMAP.md](</C:/Users/marcc/PycharmProjects/StockMarketPredictions/REAL_MONEY_EXECUTION_ROADMAP.md>)

It is written against the current repo, not a greenfield system.

## Core Thesis

This repo should not be built as:
- a magic money bot
- a generic market-direction oracle
- an options-first automation engine
- a high-frequency trading simulator pretending to be production HFT

It should be built as:

1. a market-aware trading workstation
2. a ranking and event-response engine
3. a paper-to-live execution stack with hard safety controls
4. a review and attribution system that improves process over time

That means the system should optimize for:
- point-in-time data
- narrow forecast targets
- execution realism
- capital preservation
- auditability
- post-trade learning

## What Already Exists

The repo already contains the strongest parts of the first safety and workflow layer.

Backend seams already in place:
- [C:\Users\marcc\PycharmProjects\StockMarketPredictions\backend\stock_direction_model.py](</C:/Users/marcc/PycharmProjects/StockMarketPredictions/backend/stock_direction_model.py>)
- [C:\Users\marcc\PycharmProjects\StockMarketPredictions\backend\schemas.py](</C:/Users/marcc/PycharmProjects/StockMarketPredictions/backend/schemas.py>)
- [C:\Users\marcc\PycharmProjects\StockMarketPredictions\backend\routers\market.py](</C:/Users/marcc/PycharmProjects/StockMarketPredictions/backend/routers/market.py>)
- [C:\Users\marcc\PycharmProjects\StockMarketPredictions\backend\routers\trades.py](</C:/Users/marcc/PycharmProjects/StockMarketPredictions/backend/routers/trades.py>)
- [C:\Users\marcc\PycharmProjects\StockMarketPredictions\backend\routers\portfolio.py](</C:/Users/marcc/PycharmProjects/StockMarketPredictions/backend/routers/portfolio.py>)
- [C:\Users\marcc\PycharmProjects\StockMarketPredictions\backend\services\market_service.py](</C:/Users/marcc/PycharmProjects/StockMarketPredictions/backend/services/market_service.py>)
- [C:\Users\marcc\PycharmProjects\StockMarketPredictions\backend\services\intraday_momentum_service.py](</C:/Users/marcc/PycharmProjects/StockMarketPredictions/backend/services/intraday_momentum_service.py>)
- [C:\Users\marcc\PycharmProjects\StockMarketPredictions\backend\services\alerts_service.py](</C:/Users/marcc/PycharmProjects/StockMarketPredictions/backend/services/alerts_service.py>)
- [C:\Users\marcc\PycharmProjects\StockMarketPredictions\backend\services\trade_service.py](</C:/Users/marcc/PycharmProjects/StockMarketPredictions/backend/services/trade_service.py>)
- [C:\Users\marcc\PycharmProjects\StockMarketPredictions\backend\services\portfolio_service.py](</C:/Users/marcc/PycharmProjects/StockMarketPredictions/backend/services/portfolio_service.py>)
- [C:\Users\marcc\PycharmProjects\StockMarketPredictions\backend\services\frontend_service.py](</C:/Users/marcc/PycharmProjects/StockMarketPredictions/backend/services/frontend_service.py>)
- [C:\Users\marcc\PycharmProjects\StockMarketPredictions\backend\services\notes_service.py](</C:/Users/marcc/PycharmProjects/StockMarketPredictions/backend/services/notes_service.py>)

Execution seams already in place:
- [C:\Users\marcc\PycharmProjects\StockMarketPredictions\backend\services\execution\base.py](</C:/Users/marcc/PycharmProjects/StockMarketPredictions/backend/services/execution/base.py>)
- [C:\Users\marcc\PycharmProjects\StockMarketPredictions\backend\services\execution\desk_adapter.py](</C:/Users/marcc/PycharmProjects/StockMarketPredictions/backend/services/execution/desk_adapter.py>)
- [C:\Users\marcc\PycharmProjects\StockMarketPredictions\backend\services\execution\alpaca_paper_adapter.py](</C:/Users/marcc/PycharmProjects/StockMarketPredictions/backend/services/execution/alpaca_paper_adapter.py>)
- [C:\Users\marcc\PycharmProjects\StockMarketPredictions\backend\services\execution\provider_registry.py](</C:/Users/marcc/PycharmProjects/StockMarketPredictions/backend/services/execution/provider_registry.py>)
- [C:\Users\marcc\PycharmProjects\StockMarketPredictions\backend\services\execution\mappers.py](</C:/Users/marcc/PycharmProjects/StockMarketPredictions/backend/services/execution/mappers.py>)

Market-data seams already in place:
- [C:\Users\marcc\PycharmProjects\StockMarketPredictions\backend\services\market_data\base.py](</C:/Users/marcc/PycharmProjects/StockMarketPredictions/backend/services/market_data/base.py>)
- [C:\Users\marcc\PycharmProjects\StockMarketPredictions\backend\services\market_data\provider_registry.py](</C:/Users/marcc/PycharmProjects/StockMarketPredictions/backend/services/market_data/provider_registry.py>)
- [C:\Users\marcc\PycharmProjects\StockMarketPredictions\backend\services\market_data\frames.py](</C:/Users/marcc/PycharmProjects/StockMarketPredictions/backend/services/market_data/frames.py>)
- [C:\Users\marcc\PycharmProjects\StockMarketPredictions\backend\services\market_data\intraday_provider.py](</C:/Users/marcc/PycharmProjects/StockMarketPredictions/backend/services/market_data/intraday_provider.py>)
- [C:\Users\marcc\PycharmProjects\StockMarketPredictions\backend\services\market_data\yfinance_adapter.py](</C:/Users/marcc/PycharmProjects/StockMarketPredictions/backend/services/market_data/yfinance_adapter.py>)

Primary frontend surfaces already in place:
- [C:\Users\marcc\PycharmProjects\StockMarketPredictions\frontend\src\pages\DashboardPage.jsx](</C:/Users/marcc/PycharmProjects/StockMarketPredictions/frontend/src/pages/DashboardPage.jsx>)
- [C:\Users\marcc\PycharmProjects\StockMarketPredictions\frontend\src\pages\WatchlistPage.jsx](</C:/Users/marcc/PycharmProjects/StockMarketPredictions/frontend/src/pages/WatchlistPage.jsx>)
- [C:\Users\marcc\PycharmProjects\StockMarketPredictions\frontend\src\pages\ComparePage.jsx](</C:/Users/marcc/PycharmProjects/StockMarketPredictions/frontend/src/pages/ComparePage.jsx>)
- [C:\Users\marcc\PycharmProjects\StockMarketPredictions\frontend\src\pages\AlertsPage.jsx](</C:/Users/marcc/PycharmProjects/StockMarketPredictions/frontend/src/pages/AlertsPage.jsx>)
- [C:\Users\marcc\PycharmProjects\StockMarketPredictions\frontend\src\pages\TradesPage.jsx](</C:/Users/marcc/PycharmProjects/StockMarketPredictions/frontend/src/pages/TradesPage.jsx>)
- [C:\Users\marcc\PycharmProjects\StockMarketPredictions\frontend\src\pages\PortfolioPage.jsx](</C:/Users/marcc/PycharmProjects/StockMarketPredictions/frontend/src/pages/PortfolioPage.jsx>)
- [C:\Users\marcc\PycharmProjects\StockMarketPredictions\frontend\src\pages\JournalPage.jsx](</C:/Users/marcc/PycharmProjects/StockMarketPredictions/frontend/src/pages/JournalPage.jsx>)
- [C:\Users\marcc\PycharmProjects\StockMarketPredictions\frontend\src\pages\NotesPage.jsx](</C:/Users/marcc/PycharmProjects/StockMarketPredictions/frontend/src/pages/NotesPage.jsx>)
- [C:\Users\marcc\PycharmProjects\StockMarketPredictions\frontend\src\pages\SettingsPage.jsx](</C:/Users/marcc/PycharmProjects/StockMarketPredictions/frontend/src/pages/SettingsPage.jsx>)
- [C:\Users\marcc\PycharmProjects\StockMarketPredictions\frontend\src\api\client.js](</C:/Users/marcc/PycharmProjects/StockMarketPredictions/frontend/src/api/client.js>)

The system already has meaningful progress on:
- capital-preservation rules
- review-only lockouts
- paper-order reconciliation
- prediction framing
- event and macro context
- execution realism framing
- fractional-share tiny-account mode
- journal attribution and review-loop workflows

That means the next work should deepen the research stack, not restart the safety stack.

## System Architecture

The right architecture for this repo is:

```text
Market and filing data
-> point-in-time normalization
-> feature and forecast generation
-> ranking and trade qualification
-> portfolio and risk controls
-> OMS / execution routing
-> fill and slippage capture
-> journal attribution and repair loop
-> next-session improvements
```

Each layer below should have a clear owner inside the repo.

## Layer 1: Data Acquisition and Normalization

Purpose:
- produce market, event, and macro inputs that are usable for forecasting and execution decisions

Primary owners:
- [C:\Users\marcc\PycharmProjects\StockMarketPredictions\backend\services\market_data\provider_registry.py](</C:/Users/marcc/PycharmProjects/StockMarketPredictions/backend/services/market_data/provider_registry.py>)
- [C:\Users\marcc\PycharmProjects\StockMarketPredictions\backend\services\market_data\frames.py](</C:/Users/marcc/PycharmProjects/StockMarketPredictions/backend/services/market_data/frames.py>)
- [C:\Users\marcc\PycharmProjects\StockMarketPredictions\backend\services\market_data\intraday_provider.py](</C:/Users/marcc/PycharmProjects/StockMarketPredictions/backend/services/market_data/intraday_provider.py>)
- [C:\Users\marcc\PycharmProjects\StockMarketPredictions\backend\services\market_data\yfinance_adapter.py](</C:/Users/marcc/PycharmProjects/StockMarketPredictions/backend/services/market_data/yfinance_adapter.py>)
- [C:\Users\marcc\PycharmProjects\StockMarketPredictions\backend\services\market_service.py](</C:/Users/marcc/PycharmProjects/StockMarketPredictions/backend/services/market_service.py>)

Required data families:
- daily and intraday OHLCV
- spread and liquidity proxies
- realized-volatility inputs
- earnings and event timestamps
- macro-release timestamps
- benchmark and sector references
- trade and fill history for later calibration

Rules:
- every event input should carry a usable timestamp and freshness field
- every market input should declare source, resolution, and delay characteristics
- no page should infer timing from presentation text alone

What to add next:
- explicit event-calendar normalization service
- macro-release schedule normalization
- benchmark and sector membership snapshots for ranking
- local cache layer for point-in-time replay inputs

## Layer 2: Feature and Forecast Engine

Purpose:
- turn normalized inputs into forecast families that are narrow, labeled, and auditable

Primary owners:
- [C:\Users\marcc\PycharmProjects\StockMarketPredictions\backend\stock_direction_model.py](</C:/Users/marcc/PycharmProjects/StockMarketPredictions/backend/stock_direction_model.py>)
- [C:\Users\marcc\PycharmProjects\StockMarketPredictions\backend\services\market_service.py](</C:/Users/marcc/PycharmProjects/StockMarketPredictions/backend/services/market_service.py>)
- [C:\Users\marcc\PycharmProjects\StockMarketPredictions\backend\services\intraday_momentum_service.py](</C:/Users/marcc/PycharmProjects/StockMarketPredictions/backend/services/intraday_momentum_service.py>)
- [C:\Users\marcc\PycharmProjects\StockMarketPredictions\backend\services\alerts_service.py](</C:/Users/marcc/PycharmProjects/StockMarketPredictions/backend/services/alerts_service.py>)

Forecast families this repo should treat as first-class:

1. ranking forecast
2. event-response forecast
3. volatility and regime forecast
4. execution-quality forecast
5. direction forecast as a supporting input, not the whole product

Every forecast payload should state:
- target family
- horizon
- freshness
- baseline
- confidence or calibration language
- invalidation conditions when they are known

What to avoid:
- a single generic confidence score
- unlabeled bullish or bearish output with no horizon
- pretending direction and tradability are the same prediction problem

Best next model target:
- controlled liquid-universe ranking board

Expected score decomposition:
- trend or continuation context
- event posture
- volatility posture
- execution quality
- calibration quality

## Layer 3: Trade Qualification and Desk Logic

Purpose:
- convert forecasts into tradable or stand-down decisions on the desk

Primary owners:
- [C:\Users\marcc\PycharmProjects\StockMarketPredictions\frontend\src\pages\DashboardPage.jsx](</C:/Users/marcc/PycharmProjects/StockMarketPredictions/frontend/src/pages/DashboardPage.jsx>)
- [C:\Users\marcc\PycharmProjects\StockMarketPredictions\frontend\src\pages\WatchlistPage.jsx](</C:/Users/marcc/PycharmProjects/StockMarketPredictions/frontend/src/pages/WatchlistPage.jsx>)
- [C:\Users\marcc\PycharmProjects\StockMarketPredictions\frontend\src\pages\ComparePage.jsx](</C:/Users/marcc/PycharmProjects/StockMarketPredictions/frontend/src/pages/ComparePage.jsx>)
- [C:\Users\marcc\PycharmProjects\StockMarketPredictions\frontend\src\pages\AlertsPage.jsx](</C:/Users/marcc/PycharmProjects/StockMarketPredictions/frontend/src/pages/AlertsPage.jsx>)
- [C:\Users\marcc\PycharmProjects\StockMarketPredictions\frontend\src\api\client.js](</C:/Users/marcc/PycharmProjects/StockMarketPredictions/frontend/src/api/client.js>)

Desk qualification questions should always be:
- is there a qualified setup
- is the setup within an event window
- is the expected move large enough after costs
- is the fill likely to be clean enough
- is the account allowed to take this risk today

The desk should continue to favor:
- equities before options
- limit orders before market orders
- regular-hours routing before extended-hours routing
- one-position tiny-account mode before multi-position complexity

What to build next:
- candidate board for a controlled liquid universe
- ranking-aware compare mode
- event-aware watchlist sorting
- execution drag callouts on every first-capital candidate

## Layer 4: Portfolio, Sizing, and Risk Controls

Purpose:
- ensure the system survives bad regimes and bad user impulses

Primary owners:
- [C:\Users\marcc\PycharmProjects\StockMarketPredictions\backend\schemas.py](</C:/Users/marcc/PycharmProjects/StockMarketPredictions/backend/schemas.py>)
- [C:\Users\marcc\PycharmProjects\StockMarketPredictions\backend\services\trade_service.py](</C:/Users/marcc/PycharmProjects/StockMarketPredictions/backend/services/trade_service.py>)
- [C:\Users\marcc\PycharmProjects\StockMarketPredictions\backend\services\portfolio_service.py](</C:/Users/marcc/PycharmProjects/StockMarketPredictions/backend/services/portfolio_service.py>)
- [C:\Users\marcc\PycharmProjects\StockMarketPredictions\frontend\src\pages\SettingsPage.jsx](</C:/Users/marcc/PycharmProjects/StockMarketPredictions/frontend/src/pages/SettingsPage.jsx>)
- [C:\Users\marcc\PycharmProjects\StockMarketPredictions\frontend\src\pages\TradesPage.jsx](</C:/Users/marcc/PycharmProjects/StockMarketPredictions/frontend/src/pages/TradesPage.jsx>)
- [C:\Users\marcc\PycharmProjects\StockMarketPredictions\frontend\src\pages\PortfolioPage.jsx](</C:/Users/marcc/PycharmProjects/StockMarketPredictions/frontend/src/pages/PortfolioPage.jsx>)

The current safety direction is correct:
- daily loss limits
- max active tickets
- long-only and equity-only modes
- limit-only mode
- regular-hours-only mode
- fractional-share-only tiny-account mode
- review-only lockouts

Keep these as system defaults, not optional marketing features.

What to add next:
- risk-budget-aware ranking promotion
- concentration and duplicate-exposure penalties in the candidate board
- rolling drawdown state in portfolio snapshots
- explicit paper-to-live promotion gates in settings and dashboard copy

## Layer 5: OMS, Execution Routing, and Fill Capture

Purpose:
- translate approved desk tickets into real paper or live orders without leaking broker shapes into page code

Primary owners:
- [C:\Users\marcc\PycharmProjects\StockMarketPredictions\backend\services\execution\base.py](</C:/Users/marcc/PycharmProjects/StockMarketPredictions/backend/services/execution/base.py>)
- [C:\Users\marcc\PycharmProjects\StockMarketPredictions\backend\services\execution\provider_registry.py](</C:/Users/marcc/PycharmProjects/StockMarketPredictions/backend/services/execution/provider_registry.py>)
- [C:\Users\marcc\PycharmProjects\StockMarketPredictions\backend\services\execution\alpaca_paper_adapter.py](</C:/Users/marcc/PycharmProjects/StockMarketPredictions/backend/services/execution/alpaca_paper_adapter.py>)
- [C:\Users\marcc\PycharmProjects\StockMarketPredictions\backend\services\execution\mappers.py](</C:/Users/marcc/PycharmProjects/StockMarketPredictions/backend/services/execution/mappers.py>)
- [C:\Users\marcc\PycharmProjects\StockMarketPredictions\backend\services\trade_service.py](</C:/Users/marcc/PycharmProjects/StockMarketPredictions/backend/services/trade_service.py>)

Required execution contract:
- desk ticket in
- provider-specific payload out
- normalized order and fill result back
- broker lifecycle updates reconciled into local trade state

What should remain broker-agnostic above the adapter layer:
- order intent
- route posture
- expected fill framing
- slippage review
- journal attribution

Best next execution tasks:
- transaction-cost calibration from actual paper fills
- explicit partial-fill review and cancel/replace guidance
- route-quality statistics grouped by order type and session
- broker-safe live adapter only after paper reconciliation is stable

## Layer 6: Monitoring, Review, and Repair Loop

Purpose:
- turn results into process improvement instead of only PnL history

Primary owners:
- [C:\Users\marcc\PycharmProjects\StockMarketPredictions\backend\services\portfolio_service.py](</C:/Users/marcc/PycharmProjects/StockMarketPredictions/backend/services/portfolio_service.py>)
- [C:\Users\marcc\PycharmProjects\StockMarketPredictions\backend\services\frontend_service.py](</C:/Users/marcc/PycharmProjects/StockMarketPredictions/backend/services/frontend_service.py>)
- [C:\Users\marcc\PycharmProjects\StockMarketPredictions\backend\services\notes_service.py](</C:/Users/marcc/PycharmProjects/StockMarketPredictions/backend/services/notes_service.py>)
- [C:\Users\marcc\PycharmProjects\StockMarketPredictions\frontend\src\pages\JournalPage.jsx](</C:/Users/marcc/PycharmProjects/StockMarketPredictions/frontend/src/pages/JournalPage.jsx>)
- [C:\Users\marcc\PycharmProjects\StockMarketPredictions\frontend\src\pages\NotesPage.jsx](</C:/Users/marcc/PycharmProjects/StockMarketPredictions/frontend/src/pages/NotesPage.jsx>)
- [C:\Users\marcc\PycharmProjects\StockMarketPredictions\frontend\src\pages\DashboardPage.jsx](</C:/Users/marcc/PycharmProjects/StockMarketPredictions/frontend/src/pages/DashboardPage.jsx>)
- [C:\Users\marcc\PycharmProjects\StockMarketPredictions\frontend\src\pages\TradesPage.jsx](</C:/Users/marcc/PycharmProjects/StockMarketPredictions/frontend/src/pages/TradesPage.jsx>)

This layer should answer:
- was the thesis right
- was the execution good enough
- was the size appropriate
- did the account break its own rules
- what repair should be applied before tomorrow

The review-loop work already added in this repo is the right foundation.

What to deepen next:
- link repair outcomes back into ranking penalties
- use repeated execution drift to down-rank poor setups
- use repeated risk-review outcomes to tighten promotion gates
- keep note-derived desk cautions visible until explicitly resolved

## Data Contract Rules

These rules should apply across all backend payloads.

Every forecast-like payload should expose:
- `target_family`
- `target_label`
- `horizon_label`
- `freshness_label`
- `baseline_label`
- `event_context`
- `execution_context`

Every trade-like payload should expose:
- intended entry
- expected fill posture
- realized fill
- slippage or drift summary
- rule-block or caution reason

Every journal-like payload should expose:
- attribution outcome
- execution review outcome
- risk review outcome
- repair-loop linkage when a follow-up note exists

## Validation Standard

No forecast or strategy should be promoted because it looks good in a single backtest.

Minimum standard for this repo:

1. offline walk-forward validation
2. explicit cost assumptions
3. ranking and event logic checked against a baseline
4. paper execution with real slippage and order-lifecycle capture
5. post-trade attribution reviewed in Journal and Notes
6. only then consider first-capital promotion

Validation artifacts this repo should add next:
- saved validation summaries for each forecast family
- simple benchmark comparison output
- route-quality and slippage scorecards
- replayable candidate-board snapshots for major sessions

## Non-Goals

Do not spend the next phase on:
- full options automation
- RL-first alpha research
- raw HFT ambitions
- generic chatbot trading claims
- broad market direction as the whole product
- live-money expansion before paper execution metrics are stable

## Next Build Sequence

This is the strongest next implementation order after the current safety and review-loop work.

### Sequence 1: Controlled Liquid Ranking Board

Goal:
- rank a defined liquid universe using normalized score components

Primary targets:
- [C:\Users\marcc\PycharmProjects\StockMarketPredictions\backend\stock_direction_model.py](</C:/Users/marcc/PycharmProjects/StockMarketPredictions/backend/stock_direction_model.py>)
- [C:\Users\marcc\PycharmProjects\StockMarketPredictions\backend\services\market_service.py](</C:/Users/marcc/PycharmProjects/StockMarketPredictions/backend/services/market_service.py>)
- [C:\Users\marcc\PycharmProjects\StockMarketPredictions\frontend\src\pages\WatchlistPage.jsx](</C:/Users/marcc/PycharmProjects/StockMarketPredictions/frontend/src/pages/WatchlistPage.jsx>)
- [C:\Users\marcc\PycharmProjects\StockMarketPredictions\frontend\src\pages\ComparePage.jsx](</C:/Users/marcc/PycharmProjects/StockMarketPredictions/frontend/src/pages/ComparePage.jsx>)

Build:
- define a liquid starter universe
- normalize score families
- rank by tradable opportunity, not raw direction
- show score decomposition in compare and watchlist

### Sequence 2: Event and Macro Calendar Core

Goal:
- make event timing first-class, not implied

Primary targets:
- [C:\Users\marcc\PycharmProjects\StockMarketPredictions\backend\services\market_service.py](</C:/Users/marcc/PycharmProjects/StockMarketPredictions/backend/services/market_service.py>)
- [C:\Users\marcc\PycharmProjects\StockMarketPredictions\backend\services\alerts_service.py](</C:/Users/marcc/PycharmProjects/StockMarketPredictions/backend/services/alerts_service.py>)
- [C:\Users\marcc\PycharmProjects\StockMarketPredictions\frontend\src\pages\DashboardPage.jsx](</C:/Users/marcc/PycharmProjects/StockMarketPredictions/frontend/src/pages/DashboardPage.jsx>)
- [C:\Users\marcc\PycharmProjects\StockMarketPredictions\frontend\src\pages\AlertsPage.jsx](</C:/Users/marcc/PycharmProjects/StockMarketPredictions/frontend/src/pages/AlertsPage.jsx>)

Build:
- normalized event windows
- macro-release proximity
- explicit stand-down vs caution states
- candidate-board penalties near fragile windows

### Sequence 3: Validation and Replay Artifacts

Goal:
- make research claims inspectable inside the product

Primary targets:
- [C:\Users\marcc\PycharmProjects\StockMarketPredictions\backend\services\market_service.py](</C:/Users/marcc/PycharmProjects/StockMarketPredictions/backend/services/market_service.py>)
- [C:\Users\marcc\PycharmProjects\StockMarketPredictions\backend\services\portfolio_service.py](</C:/Users/marcc/PycharmProjects/StockMarketPredictions/backend/services/portfolio_service.py>)
- [C:\Users\marcc\PycharmProjects\StockMarketPredictions\frontend\src\pages\JournalPage.jsx](</C:/Users/marcc/PycharmProjects/StockMarketPredictions/frontend/src/pages/JournalPage.jsx>)
- [C:\Users\marcc\PycharmProjects\StockMarketPredictions\frontend\src\pages\PortfolioPage.jsx](</C:/Users/marcc/PycharmProjects/StockMarketPredictions/frontend/src/pages/PortfolioPage.jsx>)

Build:
- forecast family scorecards
- simple benchmark comparisons
- candidate-board snapshot history
- slippage and route review summaries

### Sequence 4: Paper-to-Live Promotion Gates

Goal:
- make the app say clearly when a setup is research-only, paper-ready, or first-capital-ready

Primary targets:
- [C:\Users\marcc\PycharmProjects\StockMarketPredictions\backend\services\trade_service.py](</C:/Users/marcc/PycharmProjects/StockMarketPredictions/backend/services/trade_service.py>)
- [C:\Users\marcc\PycharmProjects\StockMarketPredictions\backend\services\frontend_service.py](</C:/Users/marcc/PycharmProjects/StockMarketPredictions/backend/services/frontend_service.py>)
- [C:\Users\marcc\PycharmProjects\StockMarketPredictions\frontend\src\pages\DashboardPage.jsx](</C:/Users/marcc/PycharmProjects/StockMarketPredictions/frontend/src/pages/DashboardPage.jsx>)
- [C:\Users\marcc\PycharmProjects\StockMarketPredictions\frontend\src\pages\SettingsPage.jsx](</C:/Users/marcc/PycharmProjects/StockMarketPredictions/frontend/src/pages/SettingsPage.jsx>)

Build:
- explicit promotion statuses
- minimum paper-track record thresholds
- execution-quality thresholds
- review-loop cleanliness checks before live expansion

### Sequence 5: Live Broker Rollout Only After Paper Stability

Goal:
- use the same normalized execution contract for live routing without weakening the safety system

Primary targets:
- [C:\Users\marcc\PycharmProjects\StockMarketPredictions\backend\services\execution\provider_registry.py](</C:/Users/marcc/PycharmProjects/StockMarketPredictions/backend/services/execution/provider_registry.py>)
- [C:\Users\marcc\PycharmProjects\StockMarketPredictions\backend\services\trade_service.py](</C:/Users/marcc/PycharmProjects/StockMarketPredictions/backend/services/trade_service.py>)
- live adapter file when introduced under [C:\Users\marcc\PycharmProjects\StockMarketPredictions\backend\services\execution](</C:/Users/marcc/PycharmProjects/StockMarketPredictions/backend/services/execution>)

Build:
- live adapter behind feature flag
- broker-side lockouts
- capital-preservation defaults enforced before send
- no live rollout until paper slippage and review metrics are acceptable

## Acceptance Standard For The Blueprint

This blueprint is being followed correctly when:
- the repo produces narrower forecast types instead of generic confidence blobs
- the desk promotes trades based on tradable ranking, not excitement
- paper execution data feeds back into route and fill judgments
- risk rules stay stricter than the user’s worst impulse
- the journal and notes loop changes tomorrow’s board instead of only documenting yesterday

If those are true, this repo is becoming a serious trading workstation.

If those are not true, the app is still too close to a backtest-themed dashboard.
