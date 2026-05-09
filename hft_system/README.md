# HFT Research and Simulation System

Standalone high-frequency trading research and simulation codebase.

This project is intentionally isolated from the slower multi-desk trading platform in the parent workspace. It does not import `backend`, `frontend`, or any shared execution, risk, strategy, or deployment logic from that system.

## Scope

- Common-stock and ETF microstructure replay and simulation
- Order book reconstruction
- Market microstructure feature research
- Inventory-aware market making simulation
- Latency-aware execution simulation
- Local HFT risk controls and kill switches
- Deterministic replay, attribution, and reporting

## Quickstart

```bash
cd hft_system
python -m venv .venv
.venv\Scripts\pip install -e .
.venv\Scripts\python -m unittest discover -s tests -t .
```

## Project layout

- `configs/` runtime configuration samples
- `data/raw/` immutable event drops in JSONL
- `data/normalized/` normalized datasets
- `data/replay/` replay outputs per run
- `hft/` runtime package
  - `hft/fair_value/` fair-value estimation package
  - `hft/latency/` latency profile and sampling package
- `tests/` standalone HFT test suite

## Notes

- V1 research is explicitly built for regular listed equities and ETFs as well as any other symbol-normalized replay stream. It does not assume option chains, strikes, expirations, Greeks, or broker option-routing semantics.
- Parquet and DuckDB support are enabled when optional dependencies are installed from `pyproject.toml`.
- HTML reporting falls back to plain HTML if Plotly is unavailable.
- No live trading connector is implemented.

## Future-only elite execution feasibility boundary

This HFT research package and the main Quant Evidence OS evidence-control plane are not current HFT or elite execution platforms. Current paper evidence, benchmark metrics, execution-quality metrics, Alpaca paper runtime results, millisecond decision-loop measurements, and evidence-control-plane readiness records are not HFT proof.

Current boundary:

- No direct market access is claimed.
- No exchange connectivity is claimed.
- No colocation is claimed.
- No smart order routing is claimed.
- No queue-position modeling claim is made for production execution.
- No nanosecond or microsecond execution-control claim is made.
- No current UI should imply HFT capability.
- Any HFT mention must be framed as future feasibility only.
- A separate written approval is required before any HFT infrastructure work.

### HFT feasibility data requirements

A future HFT thesis must define data requirements before implementation:

- Tick data.
- Order book data.
- Venue data.
- Latency telemetry.

The feasibility study must define data vendor provenance, retention expectations, clock synchronization assumptions, replay determinism, and whether data rights allow the intended research and production use.

### Market microstructure research plan

Before any future HFT build, the research plan must cover spread dynamics, queue dynamics, adverse selection, fill probability, inventory risk, and venue-regime behavior. This plan is research-only and must not change broker routes, order behavior, risk gates, kill switches, or ranking weights.

### Venue analysis plan

Before any future HFT build, venue analysis must cover fee and rebate models, displayed liquidity, hidden-liquidity assumptions, queue priority, latency profile, routing constraints, and regulatory obligations.

Venue analysis in this repository is planning evidence only. It does not enable smart order routing, direct market access, exchange connectivity, or live broker-route changes.

### Exchange-grade kill switch requirements

Future exchange-grade kill switch research must define controls for venue disconnects, latency spikes, order-rate limits, max-loss limits, inventory limits, stuck-order detection, and manual supervisor stops.

These requirements are documented for future study only. They do not modify current risk gate logic, clear current kill switches, bypass current kill switches, or enable live-money autonomy.

### HFT governance prerequisites

Before any HFT infrastructure work, a separate approval record must list legal, compliance, vendor, capital, security, and operating requirements. That approval must be outside the current evidence-control-plane roadmap and must explicitly address direct market access, exchange connectivity, colocation, low-latency market data, smart routing, market access controls, supervision, books and records, incident response, and external review.

Without that separate approval, the only allowed HFT work is feasibility documentation and isolated research review.

### HFT future-only test plan

A future-only test plan must cover latency distribution, order book reconstruction, queue model quality, and kill switch response. Passing current paper tests or current evidence-control-plane tests must not be treated as HFT proof.

### HFT claims to avoid

Avoid these claims for the current platform:

- HFT platform.
- Direct-market-access system.
- Exchange-colocated execution.
- Smart-order-routing system.
- Elite execution platform.
- Nanosecond or microsecond execution controls.
- Institutional execution infrastructure.

Allowed wording remains future-only feasibility study, isolated HFT research package, market microstructure research, and paper or replay simulation where supported by evidence.

### Future proof metrics

Future HFT proof metrics, if a separate HFT thesis is approved, must include latency distribution, market data latency, order acknowledgement latency, fill probability, queue position accuracy, venue routing performance, execution cost versus venue, and kill switch response time.

These metrics are future proof requirements only. They are not current product claims.

## Live-market paper activation

The standalone package now includes an HFT-only paper-live runtime for common stocks and ETFs.

Example entrypoints:

```bash
.venv\Scripts\hft-check-feed --env-file ..\.env.staging --symbols AAPL,SPY
.venv\Scripts\hft-run-session --env-file ..\.env.staging --base-dir data --symbols AAPL --cycles 10
.venv\Scripts\hft-stop-session --base-dir data
.venv\Scripts\hft-flatten-paper --env-file ..\.env.staging --symbols AAPL
.venv\Scripts\hft-session-report --base-dir data
```

This runtime stays paper-only. It does not share broker adapters, routers, or risk code with the main platform.

## Millisecond decision runtime

The millisecond runtime keeps the decision loop in memory, measures p50/p95/p99 decision latency, and can either dry-run
qualified order intents or send them to Alpaca paper through the existing HFT paper adapter.

Example dry-run:

```bash
.venv\Scripts\hft-run-millisecond-engine --env-file ..\.env.staging --symbols AAPL --cycles 20 --poll-interval-ms 10
```

Example Alpaca paper submission:

```bash
.venv\Scripts\hft-run-millisecond-engine --env-file ..\.env.staging --symbols AAPL --cycles 20 --poll-interval-ms 10 --submit-paper
```

Example supervised market-hours watchdog:

```bash
.venv\Scripts\hft-watch-millisecond-engine --env-file ..\.env.staging --symbols AAPL --submit-paper
```

The watchdog preflights at 09:25 ET, starts slices at 09:35 ET, stops new paper orders at 15:55 ET, and writes
evidence under `data/millisecond_watchdog/run_id=<run_id>/watchdog_summary.json`. Use `scripts\watch-millisecond-engine.ps1`
for a PowerShell launcher that can wait for the market window.

Operating boundary:

- Internal signal decisions can be made in milliseconds.
- Broker submission is still network/API limited and is not exchange-colocated HFT.
- Orders are limit-only through the paper adapter, with quote freshness gates, spread gates, risk checks, and order throttles.
- Runtime reports are written under `data/millisecond/run_id=<run_id>/session_summary.json`.

## Optimization workflow

The standalone optimizer lives under `hft.optimization` and wraps the existing deterministic replay core.

Example entrypoints:

```bash
.venv\Scripts\python -m hft.optimization.cli calibrate --input data\replay\sample.jsonl --output data\replay\optimization\calibration
.venv\Scripts\python -m hft.optimization.cli search --session-dir data\replay\sessions --output data\replay\optimization
.venv\Scripts\python -m hft.optimization.cli validate --run-dir data\replay\optimization\run_id=optimization-000001
.venv\Scripts\python -m hft.optimization.cli champion-report --run-dir data\replay\optimization\run_id=optimization-000001
```

Optimizer artifacts are written under `data/replay/optimization/run_id=<id>/` and include split manifests, calibration artifacts, candidate configs, fold metrics, selected champion output, holdout report, and an HTML champion report.
