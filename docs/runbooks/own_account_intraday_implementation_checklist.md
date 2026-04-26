# Own-Account Intraday Implementation Checklist

Use this checklist before promoting any intraday strategy from research into live trading on your own account.

## 1. Scope Lock

- [ ] The strategy is for own-account trading only.
- [ ] No customer routing, custody, or broker-dealer responsibilities are in scope.
- [ ] One market is selected for the first live deployment.
- [ ] One setup family is selected for the first live deployment.
- [ ] One broker execution path is selected.

## 2. Market and Strategy Choice

- [ ] The target instrument is liquid enough for the intended size.
- [ ] The strategy does not depend on latency-sensitive edge.
- [ ] The strategy does not depend on passive queue capture unless full-depth data and fill modeling support it.
- [ ] The setup is tied to a concrete intraday event or market structure condition.
- [ ] Entry, exit, invalidation, and no-trade conditions are explicitly defined.

## 3. Data Readiness

- [ ] Security master data is point-in-time correct.
- [ ] Splits, dividends, and symbol changes are handled correctly.
- [ ] Trading calendar, halts, short sessions, and auction windows are modeled.
- [ ] Research uses tick trades and quotes where execution realism matters.
- [ ] Data depth matches the strategy:
  - [ ] L1 is enough for aggressive event-driven execution.
  - [ ] L2 is present if spread/depth conditions affect routing.
  - [ ] Full depth or MBO is present if passive queue behavior is part of the edge.
- [ ] Raw and normalized market data are both retained for replay and debugging.

## 4. Research and Backtest Quality

- [ ] The idea first showed effect size in a simple prototype.
- [ ] The production backtest is event-driven, not bar-only where that would distort fills.
- [ ] Spread, fees, slippage, and delay are modeled explicitly.
- [ ] Fill assumptions match the execution style.
- [ ] Walk-forward validation is complete.
- [ ] Cost stress tests are complete.
- [ ] Delayed-entry tests are complete.
- [ ] The number of tested strategy variants is documented.
- [ ] Promotion decisions are not based on a single best backtest.

## 5. Execution Stack

- [ ] Strategy logic is separate from execution logic.
- [ ] A broker adapter exists for the chosen route.
- [ ] Order, fill, reject, and cancel events are persisted.
- [ ] The system can reconcile local state against broker state.
- [ ] A manual flatten path exists and has been tested.
- [ ] Shadow mode exists for dry-run production checks.

## 6. Risk Controls

- [ ] Max order size is enforced.
- [ ] Max notional per trade is enforced.
- [ ] Max net position is enforced.
- [ ] Daily loss cap is enforced outside strategy logic.
- [ ] Per-symbol exposure cap is enforced.
- [ ] Duplicate-order suppression is implemented.
- [ ] Stale-data detection blocks new orders.
- [ ] Reject spikes or auth failures trigger automatic halt logic.
- [ ] A kill switch exists and has been tested.

## 7. Operational Readiness

- [ ] Clock sync is verified before market open.
- [ ] Market status is verified before strategy start.
- [ ] Broker auth and reconnect path are verified.
- [ ] Logging and metrics are visible before trading begins.
- [ ] Alert destinations are verified.
- [ ] Current config version is recorded in the audit trail.
- [ ] The system can explain every order decision after the fact.

## 8. Paper and Shadow Trading

- [ ] Paper trading has run long enough to observe multiple intraday regimes.
- [ ] Shadow trading uses the same code path as live execution.
- [ ] Paper and shadow logs are reviewed against expected fills.
- [ ] Large differences between modeled and observed slippage are explained.
- [ ] No unexplained stale-state or order-state issues remain.

## 9. Live Promotion Gate

- [ ] First live size is intentionally tiny.
- [ ] Only one symbol or one instrument is traded initially.
- [ ] Flat-by-close behavior is enforced unless overnight risk is part of the design.
- [ ] Major scheduled event blocks are configured where appropriate.
- [ ] Promotion criteria are defined in advance:
  - [ ] positive expectancy after costs
  - [ ] acceptable live slippage versus model
  - [ ] no major operational failures
  - [ ] no unexplained divergence between research and live behavior

## 10. No-Go Conditions

Stop promotion or halt live trading if any of the following are true:

- [ ] Live slippage is materially worse than modeled.
- [ ] Passive fills are much less frequent than assumed.
- [ ] The strategy only works in one narrow regime.
- [ ] Control failures or stale-state incidents recur.
- [ ] PnL cannot be decomposed into signal, cost, and execution components.
- [ ] The system is profitable only under unrealistic assumptions.

## Minimum Success Standard

The first live milestone is not income replacement. The first live milestone is a stable process that:

- [ ] survives live execution after costs
- [ ] stays inside hard risk limits
- [ ] produces explainable fills and PnL
- [ ] can be maintained without hidden operational drift
