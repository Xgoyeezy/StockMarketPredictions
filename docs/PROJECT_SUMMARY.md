# Quant Evidence OS Project Summary

Quant Evidence OS, implemented in this repository as `StockMarketPredictions`, is an Alpaca-paper-first trading control plane for systematic market research, paper automation, operational monitoring, and evidence-based strategy improvement. It is not just a signal bot. The core product is a decision-evidence operating system that records what the platform saw, why a candidate was allowed or blocked, what happened afterward, and which rules or models deserve review.

## What It Does

- Runs a FastAPI backend and React/Vite frontend for a professional trading workstation.
- Scans equities and ETFs through multiple strategy desks, including intraday momentum, fast/scalper-style evidence, stat-arb style evidence, swing positioning, and macro/proxy workflows.
- Keeps unattended execution constrained to Alpaca paper mode unless separate live-control workflows are explicitly enabled later.
- Maintains hard safety gates: kill switch, route enforcement, stale-data protection, reconciliation checks, duplicate-order guards, cooldowns, objective/loss locks, open heat, and daily risk caps.
- Shows a Market Watchdog surface that answers whether the system is alive, connected, scanning, and allowed to trade.
- Produces candidate diagnostics, no-trade explanations, market-session state, market-day reports, paper readiness, HFT watchdog status, and execution-quality evidence.
- Uses AI as an evidence referee in a controlled role. AI can review, explain, and classify evidence, but it cannot submit orders or override risk gates.
- Tracks Evidence 100M progress from real observed evidence while keeping simulation evidence separate.
- Adds Evidence Edge Analytics and Evidence Reward research layers to convert raw evidence into measurable edge reports.

## Evidence-To-Edge Layers

The newest research layers focus on proving whether evidence actually predicts better outcomes.

- Evidence Edge Analytics asks which blockers prevented losses, which blockers wrongly blocked winners, which setup features predicted favorable outcomes, which engines worked by regime, and which setups deserve manual ranking review.
- Evidence Reward scores only timestamped prediction contracts. A row is rewardable only if it was defined before the move and includes direction, horizon, target, invalidation, confidence, actual forward return, and baseline forward return.
- Visual labels such as "bullish chart" or setup names alone do not get rewarded.
- Reward components remain transparent: baseline-adjusted return, direction correctness, target hit, adverse excursion, timing, confidence calibration, invalidation hits, missed targets, high-confidence wrong calls, and baseline underperformance.

## Main Product Surfaces

- `/live` - live console and Market Watchdog view.
- `/evidence-reward` - research-only prediction-contract reward analytics.
- `/forecast-validation` - forward-only forecast validation.
- `/execution-quality` - slippage, spread, latency, and fill-quality evidence.
- `/risk` - risk-control view.
- `/audit` - audit replay and proof trail.
- `/strategies` - strategy lifecycle and readiness surfaces.
- `/settings` - account, route, onboarding, and operating configuration surfaces.

## Important Backend APIs

- `/api/healthz` and `/api/readyz`
- `/api/orgs/trade-automation`
- `/api/orgs/trade-automation/watchdog`
- `/api/orgs/trade-automation/safety-state`
- `/api/orgs/trade-automation/market-session`
- `/api/orgs/trade-automation/no-trade-report`
- `/api/orgs/trade-automation/market-day-report`
- `/api/orgs/trade-automation/desks`
- `/api/orgs/trade-automation/candidate-diagnostics`
- `/api/orgs/trade-automation/alpaca-paper-readiness`
- `/api/orgs/trade-automation/evidence-reward/summary`
- `/api/evidence-reward/summary`

## Safety Boundary

The project is designed to fail closed. Research, AI, simulation, rewards, and analytics can influence reports and future review, but they do not directly place orders, clear kill switches, enable live trading, bypass risk gates, or change broker routes. Alpaca paper remains the only unattended execution lane in the current operating posture.

## Current Strategic Value

The differentiated edge is not raw high-frequency speed. The edge is evidence quality: every candidate, blocker, trade, missed move, model review, and operational state becomes auditable data. That creates a research loop where the system can learn which blockers help, which filters are too strict, which setups work in which regimes, and whether prediction contracts are outperforming baselines.

## Professional Planning Docs

- `docs/CURRENT_STATE_AND_PROFESSIONAL_RATINGS.md` - current estimated readiness ratings, category-by-category comparison, grey area clarifications, build-vs-buy boundaries, and what not to claim.
- `docs/TEN_OUT_OF_TEN_ROADMAP.md` - staged roadmap for proof, data, risk, execution, governance, human comparison, and product layers.
- `docs/PROFESSIONAL_BENCHMARK_SUITE.md` - benchmark plan for baselines, metrics, walk-forward validation, cost modeling, pass/fail rules, and safety boundaries.
- `docs/PRODUCT_POSITIONING_AND_BUYER_CATEGORIES.md` - buyer categories, positioning, demo emphasis, pricing direction, and claims to avoid.
