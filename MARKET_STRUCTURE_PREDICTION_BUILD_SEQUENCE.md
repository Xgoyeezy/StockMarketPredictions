# Market Structure and Prediction Build Sequence

This is the repo-specific execution plan for:
- [C:\Users\marcc\PycharmProjects\StockMarketPredictions\MARKET_STRUCTURE_PREDICTION_ROADMAP.md](</C:/Users/marcc/PycharmProjects/StockMarketPredictions/MARKET_STRUCTURE_PREDICTION_ROADMAP.md>)

It maps the market-structure and prediction roadmap to surfaces that already exist in this repo.

## Guiding Rule

Do not build “prediction” as a floating score without workflow context.

Build on top of the current app so every model output is tied to:
- a target
- a horizon
- an execution context
- a trust layer

## Primary Surfaces Already in Place

Frontend:
- [C:\Users\marcc\PycharmProjects\StockMarketPredictions\frontend\src\pages\DashboardPage.jsx](</C:/Users/marcc/PycharmProjects/StockMarketPredictions/frontend/src/pages/DashboardPage.jsx>)
- [C:\Users\marcc\PycharmProjects\StockMarketPredictions\frontend\src\pages\ComparePage.jsx](</C:/Users/marcc/PycharmProjects/StockMarketPredictions/frontend/src/pages/ComparePage.jsx>)
- [C:\Users\marcc\PycharmProjects\StockMarketPredictions\frontend\src\pages\WatchlistPage.jsx](</C:/Users/marcc/PycharmProjects/StockMarketPredictions/frontend/src/pages/WatchlistPage.jsx>)
- [C:\Users\marcc\PycharmProjects\StockMarketPredictions\frontend\src\pages\PortfolioPage.jsx](</C:/Users/marcc/PycharmProjects/StockMarketPredictions/frontend/src/pages/PortfolioPage.jsx>)
- [C:\Users\marcc\PycharmProjects\StockMarketPredictions\frontend\src\pages\AlertsPage.jsx](</C:/Users/marcc/PycharmProjects/StockMarketPredictions/frontend/src/pages/AlertsPage.jsx>)
- [C:\Users\marcc\PycharmProjects\StockMarketPredictions\frontend\src\pages\EducationPage.jsx](</C:/Users/marcc/PycharmProjects/StockMarketPredictions/frontend/src/pages/EducationPage.jsx>)
- [C:\Users\marcc\PycharmProjects\StockMarketPredictions\frontend\src\components\CustomMarketChart.jsx](</C:/Users/marcc/PycharmProjects/StockMarketPredictions/frontend/src/components/CustomMarketChart.jsx>)
- [C:\Users\marcc\PycharmProjects\StockMarketPredictions\frontend\src\api\client.js](</C:/Users/marcc/PycharmProjects/StockMarketPredictions/frontend/src/api/client.js>)
- [C:\Users\marcc\PycharmProjects\StockMarketPredictions\frontend\src\styles.css](</C:/Users/marcc/PycharmProjects/StockMarketPredictions/frontend/src/styles.css>)

Backend:
- [C:\Users\marcc\PycharmProjects\StockMarketPredictions\backend\routers\market.py](</C:/Users/marcc/PycharmProjects/StockMarketPredictions/backend/routers/market.py>)
- [C:\Users\marcc\PycharmProjects\StockMarketPredictions\backend\routers\portfolio.py](</C:/Users/marcc/PycharmProjects/StockMarketPredictions/backend/routers/portfolio.py>)
- [C:\Users\marcc\PycharmProjects\StockMarketPredictions\backend\routers\trades.py](</C:/Users/marcc/PycharmProjects/StockMarketPredictions/backend/routers/trades.py>)
- [C:\Users\marcc\PycharmProjects\StockMarketPredictions\backend\services\market_service.py](</C:/Users/marcc/PycharmProjects/StockMarketPredictions/backend/services/market_service.py>)
- [C:\Users\marcc\PycharmProjects\StockMarketPredictions\backend\services\portfolio_service.py](</C:/Users/marcc/PycharmProjects/StockMarketPredictions/backend/services/portfolio_service.py>)
- [C:\Users\marcc\PycharmProjects\StockMarketPredictions\backend\services\trade_service.py](</C:/Users/marcc/PycharmProjects/StockMarketPredictions/backend/services/trade_service.py>)
- [C:\Users\marcc\PycharmProjects\StockMarketPredictions\backend\stock_direction_model.py](</C:/Users/marcc/PycharmProjects/StockMarketPredictions/backend/stock_direction_model.py>)

---

## Sequence 1: Market Structure on the Desk

Goal:
- make the dashboard execution rail and chart understand market structure as first-class context

Frontend targets:
- [C:\Users\marcc\PycharmProjects\StockMarketPredictions\frontend\src\pages\DashboardPage.jsx](</C:/Users/marcc/PycharmProjects/StockMarketPredictions/frontend/src/pages/DashboardPage.jsx>)
- [C:\Users\marcc\PycharmProjects\StockMarketPredictions\frontend\src\components\CustomMarketChart.jsx](</C:/Users/marcc/PycharmProjects/StockMarketPredictions/frontend/src/components/CustomMarketChart.jsx>)
- [C:\Users\marcc\PycharmProjects\StockMarketPredictions\frontend\src\styles.css](</C:/Users/marcc/PycharmProjects/StockMarketPredictions/frontend/src/styles.css>)

Backend targets:
- [C:\Users\marcc\PycharmProjects\StockMarketPredictions\backend\services\market_service.py](</C:/Users/marcc/PycharmProjects/StockMarketPredictions/backend/services/market_service.py>)
- [C:\Users\marcc\PycharmProjects\StockMarketPredictions\backend\routers\market.py](</C:/Users/marcc/PycharmProjects/StockMarketPredictions/backend/routers/market.py>)

Add:
- route-quality summaries
- session-aware order guidance
- spread-quality classifications
- order-type suggestions based on liquidity
- simple execution-risk flags

Acceptance:
- the desk explains why execution quality changes by session, spread, and route choice

## Sequence 2: Prediction Target Clarity

Goal:
- stop presenting all predictions as one generic “signal”

Frontend targets:
- [C:\Users\marcc\PycharmProjects\StockMarketPredictions\frontend\src\pages\DashboardPage.jsx](</C:/Users/marcc/PycharmProjects/StockMarketPredictions/frontend/src/pages/DashboardPage.jsx>)
- [C:\Users\marcc\PycharmProjects\StockMarketPredictions\frontend\src\pages\ComparePage.jsx](</C:/Users/marcc/PycharmProjects/StockMarketPredictions/frontend/src/pages/ComparePage.jsx>)

Backend targets:
- [C:\Users\marcc\PycharmProjects\StockMarketPredictions\backend\stock_direction_model.py](</C:/Users/marcc/PycharmProjects/StockMarketPredictions/backend/stock_direction_model.py>)
- [C:\Users\marcc\PycharmProjects\StockMarketPredictions\backend\services\market_service.py](</C:/Users/marcc/PycharmProjects/StockMarketPredictions/backend/services/market_service.py>)

Add:
- target type labels:
  - direction
  - volatility
  - event move
  - ranking
- horizon labels
- confidence labels by target type
- explicit caveat text for broad market direction

Acceptance:
- compare and dashboard clearly state what the model is predicting and over what horizon

## Sequence 3: Event and Macro Layer

Goal:
- bring catalysts into the active trading workflow

Frontend targets:
- [C:\Users\marcc\PycharmProjects\StockMarketPredictions\frontend\src\pages\DashboardPage.jsx](</C:/Users/marcc/PycharmProjects/StockMarketPredictions/frontend/src/pages/DashboardPage.jsx>)
- [C:\Users\marcc\PycharmProjects\StockMarketPredictions\frontend\src\pages\WatchlistPage.jsx](</C:/Users/marcc/PycharmProjects/StockMarketPredictions/frontend/src/pages/WatchlistPage.jsx>)
- [C:\Users\marcc\PycharmProjects\StockMarketPredictions\frontend\src\pages\AlertsPage.jsx](</C:/Users/marcc/PycharmProjects/StockMarketPredictions/frontend/src/pages/AlertsPage.jsx>)

Backend targets:
- [C:\Users\marcc\PycharmProjects\StockMarketPredictions\backend\services\market_service.py](</C:/Users/marcc/PycharmProjects/StockMarketPredictions/backend/services/market_service.py>)

Add:
- earnings proximity
- macro-release proximity
- filing and catalyst flags
- event-sensitive warnings in the trade ticket
- watchlist event markers

Acceptance:
- the user can tell whether a setup sits in a quiet window or an event-heavy window

## Sequence 4: Volatility and Regime Tools

Goal:
- make volatility and state detection part of the core product, not side information

Frontend targets:
- [C:\Users\marcc\PycharmProjects\StockMarketPredictions\frontend\src\pages\DashboardPage.jsx](</C:/Users/marcc/PycharmProjects/StockMarketPredictions/frontend/src/pages/DashboardPage.jsx>)
- [C:\Users\marcc\PycharmProjects\StockMarketPredictions\frontend\src\pages\ComparePage.jsx](</C:/Users/marcc/PycharmProjects/StockMarketPredictions/frontend/src/pages/ComparePage.jsx>)
- [C:\Users\marcc\PycharmProjects\StockMarketPredictions\frontend\src\pages\PortfolioPage.jsx](</C:/Users/marcc/PycharmProjects/StockMarketPredictions/frontend/src/pages/PortfolioPage.jsx>)

Backend targets:
- [C:\Users\marcc\PycharmProjects\StockMarketPredictions\backend\stock_direction_model.py](</C:/Users/marcc/PycharmProjects/StockMarketPredictions/backend/stock_direction_model.py>)
- [C:\Users\marcc\PycharmProjects\StockMarketPredictions\backend\services\market_service.py](</C:/Users/marcc/PycharmProjects/StockMarketPredictions/backend/services/market_service.py>)

Add:
- realized-volatility blocks
- IV/RV context where options data exists
- regime state labeling
- volatility-aware sizing notes
- portfolio volatility posture

Acceptance:
- the app can communicate volatility state and regime state as separate decision inputs

## Sequence 5: Research Hygiene and Trust

Goal:
- move the product away from black-box prediction claims

Frontend targets:
- [C:\Users\marcc\PycharmProjects\StockMarketPredictions\frontend\src\pages\ComparePage.jsx](</C:/Users/marcc/PycharmProjects/StockMarketPredictions/frontend/src/pages/ComparePage.jsx>)
- [C:\Users\marcc\PycharmProjects\StockMarketPredictions\frontend\src\pages\EducationPage.jsx](</C:/Users/marcc/PycharmProjects/StockMarketPredictions/frontend/src/pages/EducationPage.jsx>)
- [C:\Users\marcc\PycharmProjects\StockMarketPredictions\frontend\src\pages\DashboardPage.jsx](</C:/Users/marcc/PycharmProjects/StockMarketPredictions/frontend/src/pages/DashboardPage.jsx>)

Backend targets:
- [C:\Users\marcc\PycharmProjects\StockMarketPredictions\backend\services\market_service.py](</C:/Users/marcc/PycharmProjects/StockMarketPredictions/backend/services/market_service.py>)
- [C:\Users\marcc\PycharmProjects\StockMarketPredictions\backend\stock_direction_model.py](</C:/Users/marcc/PycharmProjects/StockMarketPredictions/backend/stock_direction_model.py>)

Add:
- freshness timestamps
- stale-data warnings
- drift or unstable-state flags
- “point-in-time / net-of-cost / walk-forward” explainer copy
- simple model-card style metadata

Acceptance:
- the user can tell when a forecast is less trustworthy and why

## Sequence 6: Education Layer

Goal:
- teach users how to think about prediction and execution correctly inside the product

Frontend targets:
- [C:\Users\marcc\PycharmProjects\StockMarketPredictions\frontend\src\pages\EducationPage.jsx](</C:/Users/marcc/PycharmProjects/StockMarketPredictions/frontend/src/pages/EducationPage.jsx>)
- [C:\Users\marcc\PycharmProjects\StockMarketPredictions\frontend\src\components\EducationCallout.jsx](</C:/Users/marcc/PycharmProjects/StockMarketPredictions/frontend/src/components/EducationCallout.jsx>)

Add:
- market-structure guide
- prediction-method guide
- cost and slippage guide
- volatility/regime guide
- model-risk guide

Acceptance:
- a serious user can learn why the platform warns them, not just see the warning

## Immediate Best Build Order

For this repo, the most practical sequence is:

1. Dashboard market-structure and execution-awareness pass
2. Compare-page prediction target clarity pass
3. Watchlist and dashboard event-risk layer
4. Volatility/regime blocks in dashboard and compare
5. Trust/governance strip across prediction surfaces
6. Education-page expansion

## Strongest Product Lesson

The repo should not try to win by making louder stock predictions.

It should win by making narrower forecasts more usable:
- better framed
- more executable
- more regime-aware
- more honest about limits

That is the most defensible path implied by the research.
