# Retail Paper Operator Guide

Quant Evidence OS is paper-first for unattended operation. This guide is for retail operators who need to reach paper-ready state, understand no-trade decisions, and review broker-paper readiness without code changes.

These instructions do not enable live trading, do not change broker routes, do not submit orders, do not bypass risk gates, do not clear kill switches, and do not grant AI order authority.

## First-Session Checklist

Use this checklist before trusting a paper session:

1. Confirm the operator surface is labeled paper-first or Alpaca paper.
2. Confirm live-money autonomy is off.
3. Review paper-mode health: ready, watching, blocked, or killed.
4. Review the current blocker and next safe action.
5. Confirm demo evidence is labeled synthetic/sample and separate from real-time market-observed evidence.
6. Review no-trade records before expecting paper orders.
7. Review paper fills, rejected paper orders, and reconciliation state after paper activity.
8. Export only sanitized support evidence when sharing proof.

Paper-ready means the paper route, data freshness, blocker state, and reconciliation checks are clear. It is not proof of alpha and not an investor performance claim.

## No-Trade Explanation Guide

Every no-trade record should identify:

- blocker
- desk
- timestamp
- next scan
- explanation

No-trade explanations should be plain-language operator evidence. They explain why the system watched, blocked, or stood down. They do not prove missed profit, proven alpha, guaranteed returns, or live-money readiness.

Safe next actions:

- If stale data is the blocker, wait for data freshness or review market-session state.
- If a kill switch or lock is active, stand down until human review.
- If route or reconciliation evidence is blocked, review paper route state and local books.
- If the setup is incomplete, wait for the next configured desk scan.

## Broker Readiness Guide

Broker readiness is a read-only paper check. It confirms whether the operator can review paper automation safely.

The broker readiness wizard should check:

- Alpaca paper mode is asserted.
- Paper credential presence is known without exposing secret values.
- Local reconciliation is reviewed.
- Pending, open, and closed paper order counts are visible.
- Rejected paper order reasons are explained.
- Broker routes remain unchanged.

The broker readiness guide must not:

- change broker routes
- submit paper or live orders
- enable autonomous live-money behavior
- bypass risk gates
- clear kill switches
- expose secrets, account IDs, broker records, raw logs, credentials, or raw local paths

## Paper Fill And Rejection Language

Paper fills are simulated execution evidence. Review fill price, spread, slippage, delay, and reconciliation state.

Rejected paper orders are evidence too. The rejection reason should be shown in plain language, and the next safe action is review, not route loosening.

## Strategy Explainers

- Macro Trend Desk: watches broad market direction and only creates paper-first candidates when macro setup and blockers agree.
- Stat Arb Desk: watches relative-value setups and requires evidence checks before a paper-first candidate is useful.
- Equities Momentum Desk: watches momentum continuation and reversal conditions while respecting blockers.
- Event-Driven Desk: watches event context and should stand down when event-risk blockers are active.
- Options Volatility Desk: watches volatility setups and must keep options evidence paper-first unless a separate approved path exists.

## Support Export Safety

Support exports must be sanitized. They must exclude secrets, broker records, raw logs, account IDs, raw local paths, credentials, and unsanitized personal data.
