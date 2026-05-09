# Personal Use Operating Boundary

This project is now scoped as a private, own-account trading workstation. It is not being prepared for sale, subscription access, white-label deployment, client account management, or public investment-advisory use.

## What This App Should Do

- Help you research tickers, compare setups, and keep live chart context visible.
- Stage trade plans with entry, target, stop, invalidation, route, size, and risk.
- Keep paper rehearsal and live-capital routing separate.
- Require explicit review before promoting any workflow from paper to live funds.
- Preserve notes, trade records, and risk decisions for your own review.

## What This App Should Not Do

- Market itself as investment advice for other people.
- Route or manage other people's money.
- Collect client advisory fees, performance fees, or transaction-based compensation.
- Hide real-money risk behind model confidence language.
- Enable unattended live trading before backend broker risk locks are enforced.

## Investment Research Boundary

The app can produce decision support: rankings, signals, trade-plan drafts, risk panels, and chart annotations. Treat those outputs as research inputs. You still make the final decision, including whether the trade fits your financial situation, risk tolerance, available capital, taxes, and account restrictions.

If the project ever shifts back toward advising others, selling access, managing accounts, or accepting compensation tied to securities recommendations, stop and get securities-law advice before continuing.

## Live Funds Boundary

Use this order of operations before personal live trading:

1. Run the local desk route until tickets, notes, and risk panels behave correctly.
2. Run broker paper execution until fills, slippage, cancel/replace, and rejected-order handling are reliable.
3. Turn on backend risk locks for max risk, max daily loss, max open positions, asset class, order type, session hours, and duplicate orders.
4. Enable live routing only with explicit environment flags and frontend confirmation.
5. Start with the tiny-account preset or similarly strict caps.

Default first-live posture:

- equities only
- long only
- limit orders only
- regular market hours only
- fractional shares when account size is small
- one open position until the workflow proves itself
- no options, shorting, margin, after-hours routing, or unattended live orders

## Repo Direction

Current priority should be personal trading quality:

- remove or ignore buyer-facing sales material
- keep the default frontend in `VITE_PERSONAL_MODE=true`
- make settings describe personal paper and personal live profiles clearly
- keep public information pages framed as local personal-use notes
- prefer risk controls and review workflows over growth, billing, tenant, or customer features
