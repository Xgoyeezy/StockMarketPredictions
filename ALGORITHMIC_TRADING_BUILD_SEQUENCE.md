# Algorithmic Trading Build Sequence

This is the repo-specific execution plan for:
- [C:\Users\marcc\PycharmProjects\StockMarketPredictions\ALGORITHMIC_TRADING_ROADMAP.md](</C:/Users/marcc/PycharmProjects/StockMarketPredictions/ALGORITHMIC_TRADING_ROADMAP.md>)

It maps the algorithmic-trading roadmap to surfaces that already exist in this codebase.

## Guiding Rule

Do not build algorithmic trading as a disconnected model lab.

Build on top of the current desk so every algorithmic output is tied to:
- a target
- a horizon
- a liquidity context
- a risk budget
- an execution path

## Primary Surfaces Already In Place

Frontend:
- [C:\Users\marcc\PycharmProjects\StockMarketPredictions\frontend\src\pages\DashboardPage.jsx](</C:/Users/marcc/PycharmProjects/StockMarketPredictions/frontend/src/pages/DashboardPage.jsx>)
- [C:\Users\marcc\PycharmProjects\StockMarketPredictions\frontend\src\pages\WatchlistPage.jsx](</C:/Users/marcc/PycharmProjects/StockMarketPredictions/frontend/src/pages/WatchlistPage.jsx>)
- [C:\Users\marcc\PycharmProjects\StockMarketPredictions\frontend\src\pages\ComparePage.jsx](</C:/Users/marcc/PycharmProjects/StockMarketPredictions/frontend/src/pages/ComparePage.jsx>)
- [C:\Users\marcc\PycharmProjects\StockMarketPredictions\frontend\src\pages\AlertsPage.jsx](</C:/Users/marcc/PycharmProjects/StockMarketPredictions/frontend/src/pages/AlertsPage.jsx>)
- [C:\Users\marcc\PycharmProjects\StockMarketPredictions\frontend\src\pages\TradesPage.jsx](</C:/Users/marcc/PycharmProjects/StockMarketPredictions/frontend/src/pages/TradesPage.jsx>)
- [C:\Users\marcc\PycharmProjects\StockMarketPredictions\frontend\src\pages\PortfolioPage.jsx](</C:/Users/marcc/PycharmProjects/StockMarketPredictions/frontend/src/pages/PortfolioPage.jsx>)
- [C:\Users\marcc\PycharmProjects\StockMarketPredictions\frontend\src\pages\JournalPage.jsx](</C:/Users/marcc/PycharmProjects/StockMarketPredictions/frontend/src/pages/JournalPage.jsx>)
- [C:\Users\marcc\PycharmProjects\StockMarketPredictions\frontend\src\pages\SettingsPage.jsx](</C:/Users/marcc/PycharmProjects/StockMarketPredictions/frontend/src/pages/SettingsPage.jsx>)
- [C:\Users\marcc\PycharmProjects\StockMarketPredictions\frontend\src\components\CustomMarketChart.jsx](</C:/Users/marcc/PycharmProjects/StockMarketPredictions/frontend/src/components/CustomMarketChart.jsx>)

Backend:
- [C:\Users\marcc\PycharmProjects\StockMarketPredictions\backend\stock_direction_model.py](</C:/Users/marcc/PycharmProjects/StockMarketPredictions/backend/stock_direction_model.py>)
- [C:\Users\marcc\PycharmProjects\StockMarketPredictions\backend\services\market_service.py](</C:/Users/marcc/PycharmProjects/StockMarketPredictions/backend/services/market_service.py>)
- [C:\Users\marcc\PycharmProjects\StockMarketPredictions\backend\services\intraday_momentum_service.py](</C:/Users/marcc/PycharmProjects/StockMarketPredictions/backend/services/intraday_momentum_service.py>)
- [C:\Users\marcc\PycharmProjects\StockMarketPredictions\backend\services\alerts_service.py](</C:/Users/marcc/PycharmProjects/StockMarketPredictions/backend/services/alerts_service.py>)
- [C:\Users\marcc\PycharmProjects\StockMarketPredictions\backend\services\portfolio_service.py](</C:/Users/marcc/PycharmProjects/StockMarketPredictions/backend/services/portfolio_service.py>)
- [C:\Users\marcc\PycharmProjects\StockMarketPredictions\backend\services\trade_service.py](</C:/Users/marcc/PycharmProjects/StockMarketPredictions/backend/services/trade_service.py>)
- [C:\Users\marcc\PycharmProjects\StockMarketPredictions\backend\services\realtime_market_service.py](</C:/Users/marcc/PycharmProjects/StockMarketPredictions/backend/services/realtime_market_service.py>)
- [C:\Users\marcc\PycharmProjects\StockMarketPredictions\backend\services\market_data\base.py](</C:/Users/marcc/PycharmProjects/StockMarketPredictions/backend/services/market_data/base.py>)
- [C:\Users\marcc\PycharmProjects\StockMarketPredictions\backend\services\market_data\provider_registry.py](</C:/Users/marcc/PycharmProjects/StockMarketPredictions/backend/services/market_data/provider_registry.py>)
- [C:\Users\marcc\PycharmProjects\StockMarketPredictions\backend\services\execution\base.py](</C:/Users/marcc/PycharmProjects/StockMarketPredictions/backend/services/execution/base.py>)
- [C:\Users\marcc\PycharmProjects\StockMarketPredictions\backend\services\execution\provider_registry.py](</C:/Users/marcc/PycharmProjects/StockMarketPredictions/backend/services/execution/provider_registry.py>)
- [C:\Users\marcc\PycharmProjects\StockMarketPredictions\backend\routers\market.py](</C:/Users/marcc/PycharmProjects/StockMarketPredictions/backend/routers/market.py>)
- [C:\Users\marcc\PycharmProjects\StockMarketPredictions\backend\routers\trades.py](</C:/Users/marcc/PycharmProjects/StockMarketPredictions/backend/routers/trades.py>)
- [C:\Users\marcc\PycharmProjects\StockMarketPredictions\backend\routers\portfolio.py](</C:/Users/marcc/PycharmProjects/StockMarketPredictions/backend/routers/portfolio.py>)

## Sequence 1: Point-In-Time Research Hygiene

Goal:
- stop treating every forecast as a generic probability and start treating each one as a timestamped research target

Backend targets:
- [C:\Users\marcc\PycharmProjects\StockMarketPredictions\backend\stock_direction_model.py](</C:/Users/marcc/PycharmProjects/StockMarketPredictions/backend/stock_direction_model.py>)
- [C:\Users\marcc\PycharmProjects\StockMarketPredictions\backend\services\market_service.py](</C:/Users/marcc/PycharmProjects/StockMarketPredictions/backend/services/market_service.py>)

Frontend targets:
- [C:\Users\marcc\PycharmProjects\StockMarketPredictions\frontend\src\pages\DashboardPage.jsx](</C:/Users/marcc/PycharmProjects/StockMarketPredictions/frontend/src/pages/DashboardPage.jsx>)
- [C:\Users\marcc\PycharmProjects\StockMarketPredictions\frontend\src\pages\ComparePage.jsx](</C:/Users/marcc/PycharmProjects/StockMarketPredictions/frontend/src/pages/ComparePage.jsx>)

Add:
- target family labels:
  - ranking
  - event response
  - expected move
  - volatility posture
- explicit data-freshness and feature timestamps
- walk-forward / resolved-sample summary fields
- baseline comparisons:
  - neutral
  - technical-only
  - calibration-only

Acceptance:
- every important forecast says what it predicts, over what horizon, from what evidence base

## Sequence 2: Cross-Sectional Ranking Engine

Goal:
- turn the model stack into a ranking engine for liquid names instead of a one-name-at-a-time opinion box

Backend targets:
- [C:\Users\marcc\PycharmProjects\StockMarketPredictions\backend\stock_direction_model.py](</C:/Users/marcc/PycharmProjects/StockMarketPredictions/backend/stock_direction_model.py>)
- [C:\Users\marcc\PycharmProjects\StockMarketPredictions\backend\services\market_service.py](</C:/Users/marcc/PycharmProjects/StockMarketPredictions/backend/services/market_service.py>)

Frontend targets:
- [C:\Users\marcc\PycharmProjects\StockMarketPredictions\frontend\src\pages\WatchlistPage.jsx](</C:/Users/marcc/PycharmProjects/StockMarketPredictions/frontend/src/pages/WatchlistPage.jsx>)
- [C:\Users\marcc\PycharmProjects\StockMarketPredictions\frontend\src\pages\ComparePage.jsx](</C:/Users/marcc/PycharmProjects/StockMarketPredictions/frontend/src/pages/ComparePage.jsx>)
- [C:\Users\marcc\PycharmProjects\StockMarketPredictions\frontend\src\pages\DashboardPage.jsx](</C:/Users/marcc/PycharmProjects/StockMarketPredictions/frontend/src/pages/DashboardPage.jsx>)

Add:
- normalized ranking score for a controlled ticker universe
- rank decomposition:
  - trend
  - event state
  - volatility state
  - execution quality
  - calibration quality
- long candidate queue
- avoid / fragile queue

Acceptance:
- the desk ranks a board of names instead of over-focusing on one symbol at a time

## Sequence 3: Event and Catalyst Engine

Goal:
- make the models event-aware instead of pretending all bars are equivalent

Backend targets:
- [C:\Users\marcc\PycharmProjects\StockMarketPredictions\backend\stock_direction_model.py](</C:/Users/marcc/PycharmProjects/StockMarketPredictions/backend/stock_direction_model.py>)
- [C:\Users\marcc\PycharmProjects\StockMarketPredictions\backend\services\alerts_service.py](</C:/Users/marcc/PycharmProjects/StockMarketPredictions/backend/services/alerts_service.py>)
- [C:\Users\marcc\PycharmProjects\StockMarketPredictions\backend\services\market_data\base.py](</C:/Users/marcc/PycharmProjects/StockMarketPredictions/backend/services/market_data/base.py>)

Frontend targets:
- [C:\Users\marcc\PycharmProjects\StockMarketPredictions\frontend\src\pages\WatchlistPage.jsx](</C:/Users/marcc/PycharmProjects/StockMarketPredictions/frontend/src/pages/WatchlistPage.jsx>)
- [C:\Users\marcc\PycharmProjects\StockMarketPredictions\frontend\src\pages\AlertsPage.jsx](</C:/Users/marcc/PycharmProjects/StockMarketPredictions/frontend/src/pages/AlertsPage.jsx>)
- [C:\Users\marcc\PycharmProjects\StockMarketPredictions\frontend\src\pages\DashboardPage.jsx](</C:/Users/marcc/PycharmProjects/StockMarketPredictions/frontend/src/pages/DashboardPage.jsx>)

Add:
- event class:
  - earnings
  - macro
  - filing
  - quiet session
- event-memory breakdown by state
- event-conditioned rank penalties and boosts
- event-specific review prompts

Acceptance:
- the desk can say whether a setup is strong in quiet conditions, fragile into events, or specifically event-driven

## Sequence 4: Volatility and Regime Stack

Goal:
- model risk state directly instead of hiding it inside one generic confidence number

Backend targets:
- [C:\Users\marcc\PycharmProjects\StockMarketPredictions\backend\stock_direction_model.py](</C:/Users/marcc/PycharmProjects/StockMarketPredictions/backend/stock_direction_model.py>)
- [C:\Users\marcc\PycharmProjects\StockMarketPredictions\backend\services\intraday_momentum_service.py](</C:/Users/marcc/PycharmProjects/StockMarketPredictions/backend/services/intraday_momentum_service.py>)
- [C:\Users\marcc\PycharmProjects\StockMarketPredictions\backend\services\market_service.py](</C:/Users/marcc/PycharmProjects/StockMarketPredictions/backend/services/market_service.py>)

Frontend targets:
- [C:\Users\marcc\PycharmProjects\StockMarketPredictions\frontend\src\pages\DashboardPage.jsx](</C:/Users/marcc/PycharmProjects/StockMarketPredictions/frontend/src/pages/DashboardPage.jsx>)
- [C:\Users\marcc\PycharmProjects\StockMarketPredictions\frontend\src\pages\ComparePage.jsx](</C:/Users/marcc/PycharmProjects/StockMarketPredictions/frontend/src/pages/ComparePage.jsx>)
- [C:\Users\marcc\PycharmProjects\StockMarketPredictions\frontend\src\pages\PortfolioPage.jsx](</C:/Users/marcc/PycharmProjects/StockMarketPredictions/frontend/src/pages/PortfolioPage.jsx>)

Add:
- realized-volatility regime labels
- expected-move posture
- trend vs mean-reversion state
- size-reduction recommendations during unstable regimes
- portfolio-level volatility posture

Acceptance:
- volatility becomes a first-class control variable in the desk, not just background data

## Sequence 5: Execution-Cost and Fill Realism

Goal:
- make algorithmic trading decisions net-of-liquidity and fill quality

Backend targets:
- [C:\Users\marcc\PycharmProjects\StockMarketPredictions\backend\services\market_service.py](</C:/Users/marcc/PycharmProjects/StockMarketPredictions/backend/services/market_service.py>)
- [C:\Users\marcc\PycharmProjects\StockMarketPredictions\backend\services\realtime_market_service.py](</C:/Users/marcc/PycharmProjects/StockMarketPredictions/backend/services/realtime_market_service.py>)
- [C:\Users\marcc\PycharmProjects\StockMarketPredictions\backend\services\market_data\base.py](</C:/Users/marcc/PycharmProjects/StockMarketPredictions/backend/services/market_data/base.py>)

Frontend targets:
- [C:\Users\marcc\PycharmProjects\StockMarketPredictions\frontend\src\pages\DashboardPage.jsx](</C:/Users/marcc/PycharmProjects/StockMarketPredictions/frontend/src/pages/DashboardPage.jsx>)
- [C:\Users\marcc\PycharmProjects\StockMarketPredictions\frontend\src\pages\ComparePage.jsx](</C:/Users/marcc/PycharmProjects/StockMarketPredictions/frontend/src/pages/ComparePage.jsx>)
- [C:\Users\marcc\PycharmProjects\StockMarketPredictions\frontend\src\pages\TradesPage.jsx](</C:/Users/marcc/PycharmProjects/StockMarketPredictions/frontend/src/pages/TradesPage.jsx>)

Add:
- spread-cost estimate
- expected fill quality estimate
- market vs limit suitability
- route-quality score
- liquidity-based size cap

Acceptance:
- the desk can explain whether a ranked setup is actually tradable after expected friction

## Sequence 6: Portfolio Construction and Risk Locks

Goal:
- stop treating signal quality as enough and start enforcing portfolio-level trade discipline

Backend targets:
- [C:\Users\marcc\PycharmProjects\StockMarketPredictions\backend\services\trade_service.py](</C:/Users/marcc/PycharmProjects/StockMarketPredictions/backend/services/trade_service.py>)
- [C:\Users\marcc\PycharmProjects\StockMarketPredictions\backend\services\portfolio_service.py](</C:/Users/marcc/PycharmProjects/StockMarketPredictions/backend/services/portfolio_service.py>)
- [C:\Users\marcc\PycharmProjects\StockMarketPredictions\backend\services\execution\base.py](</C:/Users/marcc/PycharmProjects/StockMarketPredictions/backend/services/execution/base.py>)
- [C:\Users\marcc\PycharmProjects\StockMarketPredictions\backend\services\execution\provider_registry.py](</C:/Users/marcc/PycharmProjects/StockMarketPredictions/backend/services/execution/provider_registry.py>)

Frontend targets:
- [C:\Users\marcc\PycharmProjects\StockMarketPredictions\frontend\src\pages\DashboardPage.jsx](</C:/Users/marcc/PycharmProjects/StockMarketPredictions/frontend/src/pages/DashboardPage.jsx>)
- [C:\Users\marcc\PycharmProjects\StockMarketPredictions\frontend\src\pages\PortfolioPage.jsx](</C:/Users/marcc/PycharmProjects/StockMarketPredictions/frontend/src/pages/PortfolioPage.jsx>)
- [C:\Users\marcc\PycharmProjects\StockMarketPredictions\frontend\src\pages\SettingsPage.jsx](</C:/Users/marcc/PycharmProjects/StockMarketPredictions/frontend/src/pages/SettingsPage.jsx>)

Add:
- per-trade risk budget enforcement
- daily-loss lockout
- consecutive-loss lockout
- duplicate-order guard
- single-position tiny-account mode
- no-trade when decision gate is stand-down

Acceptance:
- the algorithmic layer can refuse trades when portfolio and policy conditions are wrong

## Sequence 7: Paper Execution, Sync, and Attribution

Goal:
- connect the algorithmic desk to paper execution without losing auditability

Backend targets:
- [C:\Users\marcc\PycharmProjects\StockMarketPredictions\backend\services\execution\provider_registry.py](</C:/Users/marcc/PycharmProjects/StockMarketPredictions/backend/services/execution/provider_registry.py>)
- [C:\Users\marcc\PycharmProjects\StockMarketPredictions\backend\services\trade_service.py](</C:/Users/marcc/PycharmProjects/StockMarketPredictions/backend/services/trade_service.py>)
- existing paper adapter and broker client files under:
  - `backend/services/execution/`

Frontend targets:
- [C:\Users\marcc\PycharmProjects\StockMarketPredictions\frontend\src\pages\TradesPage.jsx](</C:/Users/marcc/PycharmProjects/StockMarketPredictions/frontend/src/pages/TradesPage.jsx>)
- [C:\Users\marcc\PycharmProjects\StockMarketPredictions\frontend\src\pages\PortfolioPage.jsx](</C:/Users/marcc/PycharmProjects/StockMarketPredictions/frontend/src/pages/PortfolioPage.jsx>)
- [C:\Users\marcc\PycharmProjects\StockMarketPredictions\frontend\src\pages\JournalPage.jsx](</C:/Users/marcc/PycharmProjects/StockMarketPredictions/frontend/src/pages/JournalPage.jsx>)

Add:
- paper broker status refresh
- broker-fill reconciliation
- signal vs execution attribution
- expected vs realized slippage
- journal tagging for:
  - thesis right / execution wrong
  - thesis wrong / execution fine
  - risk violation

Acceptance:
- the paper stack becomes a real learning loop, not just a fake order ticket

## Sequence 8: Education and Governance Layer

Goal:
- teach and govern algorithmic use inside the app instead of only adding more controls

Frontend targets:
- [C:\Users\marcc\PycharmProjects\StockMarketPredictions\frontend\src\pages\EducationPage.jsx](</C:/Users/marcc/PycharmProjects/StockMarketPredictions/frontend/src/pages/EducationPage.jsx>)
- [C:\Users\marcc\PycharmProjects\StockMarketPredictions\frontend\src\pages\SettingsPage.jsx](</C:/Users/marcc/PycharmProjects/StockMarketPredictions/frontend/src/pages/SettingsPage.jsx>)

Backend targets:
- model metadata exposed from:
  - [C:\Users\marcc\PycharmProjects\StockMarketPredictions\backend\stock_direction_model.py](</C:/Users/marcc/PycharmProjects/StockMarketPredictions/backend/stock_direction_model.py>)
  - [C:\Users\marcc\PycharmProjects\StockMarketPredictions\backend\services\trade_service.py](</C:/Users/marcc/PycharmProjects/StockMarketPredictions/backend/services/trade_service.py>)

Add:
- model-card style metadata
- freshness and sample-depth explainers
- algorithmic-risk checklist
- execution-risk explainer
- tiny-account safety guidance

Acceptance:
- the user can understand why the algorithm is allowing, downgrading, or rejecting a trade

## Immediate Best Build Order

For this repo, the strongest algorithmic-trading sequence is:

1. point-in-time target framing and ranking cleanup
2. event and regime depth
3. execution-cost realism
4. server-side risk locks
5. paper execution sync and attribution
6. only then consider live-money expansion

## Strongest Product Lesson

This repo should not try to be a generic prediction machine.

It should become a narrower algorithmic trading system that is:
- better ranked
- better timed
- better filtered
- better executed
- better governed

That is the path most supported by the research and the current codebase.
