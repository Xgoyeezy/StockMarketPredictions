# Algorithmic Trading Roadmap

This roadmap turns the algorithmic-trading research into a practical product and system plan for this repo.

The key lesson from the research is simple:

- do not anchor the product on broad market-direction prediction alone
- do not treat backtests as proof
- do not separate alpha from execution, risk, and controls

Algorithmic trading in this codebase should be built as a stack:

1. point-in-time data
2. narrow prediction targets
3. portfolio and risk logic
4. execution and cost realism
5. monitoring and governance

## Product Thesis

This repo should not try to win by promising a magic trading bot.

It should become a custom algorithmic trading workstation built around:

1. cross-sectional stock selection
2. event-aware forecasting
3. volatility and regime awareness
4. execution-aware order logic
5. hard risk controls
6. review and attribution loops

That is much more defensible than trying to predict the next index candle in isolation.

## What To Build First

These are the strongest algorithmic trading problems for this repo.

### 1. Ranking, Not Generic Direction

Best first target:
- rank a small universe of liquid names by opportunity quality

Good outputs:
- long candidate ranking
- avoid / stand-down ranking
- event-sensitive ranking
- execution-quality-adjusted ranking

Why:
- this is more stable than pure market-index direction
- it fits the existing compare, watchlist, and candidate-queue flows

### 2. Event Response Models

Best first target:
- detect when earnings, macro windows, or catalyst states change the odds

Good outputs:
- quiet window vs event window
- expected event fragility
- post-event continuation vs fade context

Why:
- this repo already surfaces event risk, trust, and memory
- event conditioning is one of the most practical edges from the research

### 3. Volatility and Regime Models

Best first target:
- forecast risk state before trying to maximize return

Good outputs:
- calm vs unstable regime
- expected move range
- realized-volatility posture
- confidence penalty during unstable states

Why:
- volatility is usually more forecastable than raw price direction
- better sizing and stand-down decisions come from regime awareness

### 4. Execution Models

Best first target:
- estimate whether the edge is tradable after spread and liquidity

Good outputs:
- fill quality
- spread drag
- route sensitivity
- notional sizing suitability
- limit vs market guidance

Why:
- many paper edges die at the execution layer
- this repo already has execution-quality, route, and market-structure surfaces

### 5. Portfolio and Risk Controls

Best first target:
- decide what should be traded together, how large, and when to stop

Good outputs:
- per-trade risk budget
- daily loss lockouts
- one-position tiny-account mode
- exposure concentration warnings
- duplicate-order rejection

Why:
- risk is a portfolio property, not just a stop-loss setting
- this is necessary before any serious paper or live automation

## What To Delay

These are lower-priority or higher-risk ideas for this repo right now.

### Delay 1: Broad Market Direction as the Main Product

Do not make the product hinge on:
- predicting SPY up/down tomorrow
- generic confidence numbers with no target framing

Use broad direction as context, not as the main edge claim.

### Delay 2: Options Automation

Do not prioritize:
- automated options execution
- multi-leg live routing
- gamma / skew / assignment-heavy automation

Why:
- options add volatility-surface, fill-quality, and assignment complexity
- the repo is much closer to safe stock automation than safe options automation

### Delay 3: Reinforcement Learning for Alpha

Do not start with:
- RL for raw signal discovery
- policy learning with unrealistic market environments

If RL appears at all, it should appear later in:
- execution scheduling
- inventory control
- dynamic routing experiments

### Delay 4: High-Frequency or Queue-Level Trading

Do not position this repo as:
- HFT
- latency-arbitrage
- microsecond queue prediction

Why:
- the data, entitlements, and infrastructure are not built for that
- the current desk is much better suited to intraday and swing decision support

## Data Priorities

The data roadmap should follow the same hierarchy as the research.

### Highest Priority Data

Build around:
- clean OHLCV bars
- real-time quote updates where available
- earnings and macro event calendars
- news and catalyst metadata
- trade and order history from the desk

### Next Priority Data

Add:
- better intraday bar quality
- direct or higher-quality quote feeds
- richer event calendars
- point-in-time factor and revision-style features

### Data Rules

Every algorithmic feature should be:
- point-in-time
- timestamped
- latency-aware
- reproducible

Never treat revised or hindsight-enriched data as if it were live data.

## Research Rules

The research process should be stricter than the model choice.

Minimum rules:
- point-in-time labels
- walk-forward or time-split validation
- dumb baseline comparisons
- net-of-cost thinking
- turnover awareness
- drift review

Backtest success should never be treated as enough on its own.

## Build Order

The best algorithmic-trading build order for this repo is:

1. point-in-time target framing and ranking cleanup
2. event and volatility feature depth
3. execution-cost and liquidity realism
4. portfolio construction and hard risk locks
5. paper execution sync and attribution
6. only then consider live-money expansion

## Best Initial Strategy Family For This Repo

If the goal is personal use with a realistic path to paper and live trading, the best starting family is:

- liquid U.S. equities
- long-only
- regular-hours only
- event- and regime-aware ranking
- execution-aware limit order flow
- strict loss locks

That fits the current product far better than options or leverage-heavy automation.

## Best Product Lesson

Algorithmic trading here should mean:

- better narrowing
- better execution
- better risk refusal
- better review

not just:

- more predictions

That is the strongest path implied by both the research and the current repo structure.
