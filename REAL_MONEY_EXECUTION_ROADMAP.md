# Real Money Execution Roadmap

This roadmap turns the current custom trading desk into a staged execution system:

1. paper broker execution
2. live broker execution
3. broker-side risk locks
4. fractional-share-only mode for very small accounts

It is written against the current repo, not a greenfield design.

## Current State

The app is not live-trading yet.

Execution is currently routed through the local desk adapter only:

- [backend/services/execution/base.py](C:/Users/marcc/PycharmProjects/StockMarketPredictions/backend/services/execution/base.py)
- [backend/services/execution/provider_registry.py](C:/Users/marcc/PycharmProjects/StockMarketPredictions/backend/services/execution/provider_registry.py)
- [backend/services/execution/desk_adapter.py](C:/Users/marcc/PycharmProjects/StockMarketPredictions/backend/services/execution/desk_adapter.py)
- [backend/services/trade_service.py](C:/Users/marcc/PycharmProjects/StockMarketPredictions/backend/services/trade_service.py)

That means the desk can:

- generate tickets
- size trades
- record working / filled / canceled / closed states
- simulate order lifecycle inside the app

But it does not currently:

- send broker orders
- sync broker fills
- hold broker order ids
- place broker-native stop / take-profit orders
- enforce broker-level lockouts

## Recommended Rollout

Do not go straight to real money. The right order is:

1. paper adapter
2. live adapter
3. risk locks
4. tiny-account mode

## Phase 1: Paper Execution Adapter

Goal: keep the current UI and order flow, but route execution to a real paper brokerage account instead of only the desk simulator.

### Backend files to add

- `backend/services/execution/alpaca_paper_adapter.py`
- `backend/services/execution/alpaca_client.py`
- `backend/services/execution/mappers.py`

### Backend files to modify

- [backend/services/execution/provider_registry.py](C:/Users/marcc/PycharmProjects/StockMarketPredictions/backend/services/execution/provider_registry.py)
- [backend/services/execution/types.py](C:/Users/marcc/PycharmProjects/StockMarketPredictions/backend/services/execution/types.py)
- [backend/services/trade_service.py](C:/Users/marcc/PycharmProjects/StockMarketPredictions/backend/services/trade_service.py)
- [backend/schemas.py](C:/Users/marcc/PycharmProjects/StockMarketPredictions/backend/schemas.py)
- [backend/core/config.py](C:/Users/marcc/PycharmProjects/StockMarketPredictions/backend/core/config.py)

### What to add

- adapter name: `alpaca_paper`
- submit / replace / cancel / close methods that call the broker
- broker order id and broker status fields in result payloads
- execution result typing for:
  - broker order id
  - submitted quantity
  - filled quantity
  - notional
  - asset class
  - fractionable
  - raw broker status

### Important design rule

The desk should stay the orchestration layer.

The adapter should translate:

- desk order ticket -> broker payload
- broker response -> internal execution result

Do not leak raw broker shapes into page code.

### Minimum paper-trading scope

Start with:

- U.S. equities only
- cash account assumptions
- long only
- market and limit orders
- cancel / replace support

Do not start with:

- options
- shorting
- margin
- trailing stops
- after-hours routing

## Phase 2: Live Broker Adapter

Goal: use the same execution contract as paper, but switch to a real-money account only after the paper path is stable.

### Backend files to add

- `backend/services/execution/alpaca_live_adapter.py`

### Backend files to modify

- [backend/services/execution/provider_registry.py](C:/Users/marcc/PycharmProjects/StockMarketPredictions/backend/services/execution/provider_registry.py)
- [backend/core/config.py](C:/Users/marcc/PycharmProjects/StockMarketPredictions/backend/core/config.py)

### Config to add

- `EXECUTION_ADAPTER=alpaca_paper|alpaca_live|desk`
- broker base urls for paper and live
- explicit live execution enable flag
- explicit live execution confirmation flag

### Safety rule

The live adapter should be impossible to activate accidentally.

Require all of:

- live adapter configured
- live trading enabled in env
- valid broker credentials present
- explicit frontend confirmation

## Phase 3: Broker-Side Risk Locks

Goal: stop relying only on user discipline and local UI copy.

### Backend files to add

- `backend/services/execution/risk_policy.py`
- `backend/services/execution/preflight.py`

### Backend files to modify

- [backend/services/trade_service.py](C:/Users/marcc/PycharmProjects/StockMarketPredictions/backend/services/trade_service.py)
- [backend/schemas.py](C:/Users/marcc/PycharmProjects/StockMarketPredictions/backend/schemas.py)

### Frontend files to modify

- [frontend/src/pages/DashboardPage.jsx](C:/Users/marcc/PycharmProjects/StockMarketPredictions/frontend/src/pages/DashboardPage.jsx)
- [frontend/src/pages/TradesPage.jsx](C:/Users/marcc/PycharmProjects/StockMarketPredictions/frontend/src/pages/TradesPage.jsx)
- [frontend/src/pages/SettingsPage.jsx](C:/Users/marcc/PycharmProjects/StockMarketPredictions/frontend/src/pages/SettingsPage.jsx)
- [frontend/src/context/PreferencesContext.jsx](C:/Users/marcc/PycharmProjects/StockMarketPredictions/frontend/src/context/PreferencesContext.jsx)

### Lock rules to enforce server-side

- max risk percent per trade
- max daily loss
- max consecutive losses
- regular-hours-only mode
- asset-class restrictions
- order-type restrictions
- no duplicate working order for same ticker
- no live route when decision gate is `Stand down`

### Strong recommendation

Risk locks should live on the backend, not just in the dashboard.

UI warnings help.
Server refusal prevents damage.

## Phase 4: Fractional-Share-Only Mode for Small Accounts

Goal: make the app usable with a tiny account like `$10` without pretending options or full-share sizing make sense.

### Backend files to add

- `backend/services/execution/account_mode.py`

### Backend files to modify

- [backend/services/trade_service.py](C:/Users/marcc/PycharmProjects/StockMarketPredictions/backend/services/trade_service.py)
- [backend/schemas.py](C:/Users/marcc/PycharmProjects/StockMarketPredictions/backend/schemas.py)

### Frontend files to modify

- [frontend/src/pages/DashboardPage.jsx](C:/Users/marcc/PycharmProjects/StockMarketPredictions/frontend/src/pages/DashboardPage.jsx)
- [frontend/src/pages/SettingsPage.jsx](C:/Users/marcc/PycharmProjects/StockMarketPredictions/frontend/src/pages/SettingsPage.jsx)

### Tiny-account mode rules

For `$10` start mode:

- equities only
- fractional shares only
- long only
- limit orders only
- regular hours only
- no options
- no after-hours
- no leverage
- no margin
- max one open position
- max notional per trade: `$5`
- daily max loss: `$1`

This mode should downgrade the desk intentionally.
That is a feature, not a bug.

## Broker Order Mapping

Current internal order types:

- `market`
- `limit`
- `stop_market`
- `stop_limit`
- `trailing_stop`

Current internal TIF values:

- `day`
- `day_ext`
- `gtc_90d`

Before live trading, add one mapping table in code:

- internal order type -> broker order type
- internal tif -> broker tif
- unsupported combinations -> hard reject

Example policy:

- tiny account mode:
  - allow `limit`
  - optionally allow `market`
  - reject all others
- standard paper mode:
  - allow `market`, `limit`, `stop_limit`
- live mode:
  - enable only after paper validation

## Data Model Additions

The current execution result objects are too thin for a real broker.

Expand execution typing in [backend/services/execution/types.py](C:/Users/marcc/PycharmProjects/StockMarketPredictions/backend/services/execution/types.py) to include:

- `broker_name`
- `broker_order_id`
- `broker_status`
- `symbol`
- `asset_class`
- `qty`
- `notional`
- `filled_qty`
- `filled_avg_price`
- `fractional`
- `submitted_at`
- `updated_at`
- `raw_response`

Also persist broker metadata into order events so the UI can show:

- submitted
- accepted
- partially filled
- filled
- canceled
- rejected

## UI Work Needed

### Dashboard

In [frontend/src/pages/DashboardPage.jsx](C:/Users/marcc/PycharmProjects/StockMarketPredictions/frontend/src/pages/DashboardPage.jsx):

- add execution destination badge:
  - `Desk sim`
  - `Paper broker`
  - `Live broker`
- add account mode badge:
  - `Tiny account`
  - `Standard`
- show broker order id when real adapter is used
- hard-lock unsupported tickets before send

### Trades

In [frontend/src/pages/TradesPage.jsx](C:/Users/marcc/PycharmProjects/StockMarketPredictions/frontend/src/pages/TradesPage.jsx):

- show broker status
- show broker order id
- show filled quantity / partial fills
- separate simulated trades from broker-routed trades

### Settings

In [frontend/src/pages/SettingsPage.jsx](C:/Users/marcc/PycharmProjects/StockMarketPredictions/frontend/src/pages/SettingsPage.jsx):

- add execution mode controls
- add paper/live toggle display
- add tiny-account mode toggle
- add fractional-only toggle
- add hard daily lockout settings

## Testing Gates

Do not go live until these gates pass:

### Paper gates

- submit market order
- submit limit order
- cancel working limit order
- replace working limit order
- sync filled order back into desk
- reject unsupported ticket cleanly

### Risk gates

- reject second open trade in tiny-account mode
- reject option ticket in tiny-account mode
- reject after-hours ticket in regular-hours-only mode
- reject trade after daily loss cap hit
- reject trade after consecutive-loss cap hit

### Live gates

- same exact flow as paper
- plus explicit user confirmation
- plus environment flag
- plus real broker credentials

## Best Starting Mode For You

Because you said you want to start with `$10`, the best first live path is:

1. build paper adapter
2. build tiny-account mode
3. test fractional-share-only flow
4. go live only with:
   - SPY
   - QQQ
   - AAPL
   - MSFT
   - NVDA

And with these restrictions:

- one position max
- one symbol at a time
- no options
- limit orders only
- regular hours only
- stop for the day after one meaningful loss

## Fastest Build Order

If the goal is practical progress, do this in order:

1. `backend/services/execution/alpaca_client.py`
2. `backend/services/execution/alpaca_paper_adapter.py`
3. expand `execution/types.py`
4. wire `provider_registry.py`
5. update `trade_service.py` event payloads
6. add broker fields to UI
7. add risk preflight service
8. add tiny-account mode
9. paper test everything
10. add `alpaca_live_adapter.py`

## Recommendation

Do not chase “turn $10 into a lot more” with options or leverage.

The right use of this app for real money is:

- build a reliable paper route
- build hard backend guardrails
- use fractional shares first
- prove discipline
- then increase capital later

That is slower, but it is the only path here that is serious.
