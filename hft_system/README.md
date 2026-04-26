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
