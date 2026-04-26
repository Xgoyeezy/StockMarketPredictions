from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[2]
TEN_X_EXPORT_DIR = REPO_ROOT / "runtime-exports" / "ten-x-stand-test"


@dataclass(frozen=True)
class TenXReference:
    starting_capital: float = 100000.0
    current_equity: float = 1900000.0
    target_multiple: float = 10.0
    known_peak_equity: float = 1900000.0
    known_drawdown_peak: float = 1800000.0
    known_drawdown_trough: float = 1300000.0
    known_max_drawdown_pct: float = 27.8


@dataclass(frozen=True)
class TenXExportResult:
    output_dir: Path
    report_path: Path
    summary_path: Path
    scenario_matrix_path: Path
    ledger_path: Path | None
    summary: dict[str, Any]


def _coerce_float(value: Any, default: float = 0.0) -> float:
    if value in (None, "", "nan"):
        return float(default)
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return float(default)
    return float(default) if math.isnan(parsed) else float(parsed)


def _safe_json(value: Any) -> Any:
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {str(key): _safe_json(inner) for key, inner in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_safe_json(item) for item in value]
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    return str(value)


def max_drawdown_pct(equity: pd.Series) -> float:
    series = pd.to_numeric(equity, errors="coerce").dropna()
    if series.empty:
        return 0.0
    running_peak = series.cummax().replace(0.0, pd.NA)
    drawdown = ((series - running_peak) / running_peak).fillna(0.0)
    return round(abs(float(drawdown.min())) * 100.0, 4)


def _equity_metrics(equity: pd.Series, *, starting_capital: float) -> dict[str, Any]:
    series = pd.to_numeric(equity, errors="coerce").dropna()
    if series.empty:
        return {
            "starting_capital": round(starting_capital, 4),
            "ending_equity": round(starting_capital, 4),
            "ending_multiple": 1.0,
            "return_pct": 0.0,
            "peak_equity": round(starting_capital, 4),
            "peak_multiple": 1.0,
            "max_drawdown_pct": 0.0,
            "worst_equity": round(starting_capital, 4),
        }
    ending = float(series.iloc[-1])
    peak = float(series.max())
    worst = float(series.min())
    return {
        "starting_capital": round(starting_capital, 4),
        "ending_equity": round(ending, 4),
        "ending_multiple": round(ending / starting_capital, 6) if starting_capital > 0 else None,
        "return_pct": round(((ending - starting_capital) / starting_capital) * 100.0, 4) if starting_capital > 0 else None,
        "peak_equity": round(peak, 4),
        "peak_multiple": round(peak / starting_capital, 6) if starting_capital > 0 else None,
        "max_drawdown_pct": max_drawdown_pct(series),
        "worst_equity": round(worst, 4),
    }


def analyze_reference_path(reference: TenXReference) -> dict[str, Any]:
    target_equity = reference.starting_capital * reference.target_multiple
    drawdown_dollars = max(reference.known_drawdown_peak - reference.known_drawdown_trough, 0.0)
    recovery_dollars = max(reference.current_equity - reference.known_drawdown_trough, 0.0)
    recovery_pct = recovery_dollars / reference.known_drawdown_trough * 100.0 if reference.known_drawdown_trough > 0 else 0.0
    current_multiple = reference.current_equity / reference.starting_capital if reference.starting_capital > 0 else 0.0
    peak_multiple = reference.known_peak_equity / reference.starting_capital if reference.starting_capital > 0 else 0.0
    drawdown_vs_start = drawdown_dollars / reference.starting_capital if reference.starting_capital > 0 else 0.0
    pass_checks = {
        "reached_10x_equity_target": reference.current_equity >= target_equity,
        "known_drawdown_under_30pct": reference.known_max_drawdown_pct <= 30.0,
        "known_drawdown_under_20pct": reference.known_max_drawdown_pct <= 20.0,
        "drawdown_dollars_less_than_5x_start": drawdown_vs_start < 5.0,
    }
    if not pass_checks["reached_10x_equity_target"]:
        state = "not_at_10x"
    elif not pass_checks["known_drawdown_under_30pct"]:
        state = "10x_reached_but_drawdown_too_large"
    elif not pass_checks["known_drawdown_under_20pct"]:
        state = "10x_reached_high_volatility"
    else:
        state = "10x_reached_controlled_reference_drawdown"
    return {
        "starting_capital": round(reference.starting_capital, 4),
        "target_multiple": round(reference.target_multiple, 4),
        "target_equity": round(target_equity, 4),
        "current_equity": round(reference.current_equity, 4),
        "current_multiple": round(current_multiple, 6),
        "known_peak_equity": round(reference.known_peak_equity, 4),
        "known_peak_multiple": round(peak_multiple, 6),
        "known_drawdown_peak": round(reference.known_drawdown_peak, 4),
        "known_drawdown_trough": round(reference.known_drawdown_trough, 4),
        "known_drawdown_dollars": round(drawdown_dollars, 4),
        "known_drawdown_vs_start_multiple": round(drawdown_vs_start, 6),
        "known_max_drawdown_pct": round(reference.known_max_drawdown_pct, 4),
        "recovery_from_trough_dollars": round(recovery_dollars, 4),
        "recovery_from_trough_pct": round(recovery_pct, 4),
        "pass_checks": pass_checks,
        "state": state,
    }


def _read_ledger_csv(path: Path) -> pd.DataFrame:
    frame = pd.read_csv(path)
    if "timestamp" in frame.columns:
        frame["timestamp"] = pd.to_datetime(frame["timestamp"], errors="coerce", utc=True)
    return frame


def _load_local_ledger(*, tenant_slug: str, starting_capital: float, ledger_csv: Path | None = None) -> tuple[pd.DataFrame, dict[str, Any]]:
    if ledger_csv is not None:
        return _read_ledger_csv(ledger_csv), {"source": "ledger_csv", "path": str(ledger_csv)}
    try:
        from backend.services import strategy_validation_service as svs
        ledger = svs.build_trade_validation_ledger(tenant_slug=tenant_slug, starting_capital=starting_capital)
        return ledger, {"source": "local_database", "tenant_slug": tenant_slug}
    except Exception as exc:
        return pd.DataFrame(), {"source": "local_database", "tenant_slug": tenant_slug, "error": str(exc)}


def _closed_trade_frame(ledger: pd.DataFrame) -> pd.DataFrame:
    if ledger.empty:
        return pd.DataFrame(columns=["timestamp", "realized_pnl", "position_cost", "slippage", "fees", "equity_after_fill"])
    frame = ledger.copy()
    if "event_type" in frame.columns:
        frame = frame[frame["event_type"].astype(str).str.lower() == "close"].copy()
    for column in ("realized_pnl", "position_cost", "slippage", "fees", "equity_after_fill"):
        if column not in frame.columns:
            frame[column] = 0.0
        frame[column] = pd.to_numeric(frame[column], errors="coerce").fillna(0.0)
    if "timestamp" not in frame.columns:
        frame["timestamp"] = pd.NaT
    return frame.reset_index(drop=True)


def _fallback_equity_from_ledger(ledger: pd.DataFrame, *, starting_capital: float) -> pd.Series:
    if ledger.empty:
        return pd.Series(dtype=float)
    if "equity_after_fill" in ledger.columns:
        series = pd.to_numeric(ledger["equity_after_fill"], errors="coerce").dropna()
        if not series.empty:
            return series.reset_index(drop=True)
    closes = _closed_trade_frame(ledger)
    if closes.empty:
        return pd.Series(dtype=float)
    return (closes["realized_pnl"].cumsum() + starting_capital).reset_index(drop=True)


def _simulate_closed_trades(
    closes: pd.DataFrame,
    *,
    starting_capital: float,
    slippage_multiplier: float = 1.0,
    extra_cost_bps: float = 0.0,
    notional_cap_multiple: float | None = None,
    stop_drawdown_pct: float | None = None,
) -> dict[str, Any]:
    if closes.empty:
        metrics = _equity_metrics(pd.Series(dtype=float), starting_capital=starting_capital)
        return {**metrics, "stopped": False, "processed_trades": 0}
    equity = float(starting_capital)
    peak = float(starting_capital)
    values: list[float] = []
    stopped = False
    processed = 0
    for _, row in closes.iterrows():
        position_cost = max(_coerce_float(row.get("position_cost"), 0.0), 0.0)
        realized = _coerce_float(row.get("realized_pnl"), 0.0)
        fees = abs(_coerce_float(row.get("fees"), 0.0))
        recorded_slippage = abs(_coerce_float(row.get("slippage"), 0.0))
        modeled_slippage = position_cost * 0.0005 if recorded_slippage <= 0.0 and position_cost > 0.0 else recorded_slippage
        extra_cost = position_cost * extra_cost_bps / 10000.0 if extra_cost_bps > 0.0 else 0.0
        scale = 1.0
        if notional_cap_multiple is not None and position_cost > 0.0:
            cap = max(equity, 1.0) * float(notional_cap_multiple)
            if position_cost > cap:
                scale = max(0.0, cap / position_cost)
        adjusted_pnl = realized * scale
        adjusted_cost = fees * scale + modeled_slippage * max(slippage_multiplier - 1.0, 0.0) * scale + extra_cost * scale
        equity += adjusted_pnl - adjusted_cost
        peak = max(peak, equity)
        values.append(equity)
        processed += 1
        if stop_drawdown_pct is not None and peak > 0:
            current_drawdown = (peak - equity) / peak * 100.0
            if current_drawdown >= float(stop_drawdown_pct):
                stopped = True
                break
    metrics = _equity_metrics(pd.Series(values, dtype="float64"), starting_capital=starting_capital)
    return {**metrics, "stopped": stopped, "processed_trades": processed}


def build_10x_scenario_matrix(ledger: pd.DataFrame, *, starting_capital: float, target_multiple: float = 10.0) -> list[dict[str, Any]]:
    closes = _closed_trade_frame(ledger)
    scenarios = [
        {"key": "v0_local_records", "label": "Current local trade records", "params": {}, "purpose": "Baseline from exported fills and closes."},
        {"key": "ten_x_cost_check_2x_slippage", "label": "10x hold check, 2x slippage", "params": {"slippage_multiplier": 2.0}, "purpose": "Checks whether 10x survives doubled execution cost."},
        {"key": "ten_x_cost_check_3x_slippage", "label": "10x hold check, 3x slippage", "params": {"slippage_multiplier": 3.0}, "purpose": "Checks whether 10x survives tripled execution cost."},
        {"key": "ten_x_next_bar_10bps", "label": "10x hold check, next-bar 10 bps penalty", "params": {"extra_cost_bps": 10.0}, "purpose": "Approximates delayed execution after signal confirmation."},
        {"key": "ten_x_next_bar_25bps", "label": "10x hold check, next-bar 25 bps penalty", "params": {"extra_cost_bps": 25.0}, "purpose": "Harder delayed-execution stress test."},
        {"key": "one_x_cap", "label": "1x gross notional cap", "params": {"notional_cap_multiple": 1.0}, "purpose": "Tests whether the signal works without leverage dependence."},
        {"key": "two_x_cap", "label": "2x gross notional cap", "params": {"notional_cap_multiple": 2.0}, "purpose": "Tests whether moderate leverage keeps the edge alive."},
        {"key": "three_x_cap", "label": "3x gross notional cap", "params": {"notional_cap_multiple": 3.0}, "purpose": "Tests whether controlled leverage keeps the edge alive."},
        {"key": "ten_x_cap", "label": "10x paper gross notional cap", "params": {"notional_cap_multiple": 10.0}, "purpose": "Tests the aggressive paper-only 10x gross exposure ceiling."},
        {"key": "ten_pct_drawdown_stop", "label": "10% drawdown stop", "params": {"stop_drawdown_pct": 10.0}, "purpose": "Shows what happens if deployment stops at a 10% drawdown."},
        {"key": "twenty_pct_drawdown_stop", "label": "20% drawdown stop", "params": {"stop_drawdown_pct": 20.0}, "purpose": "Shows what happens if deployment stops at a 20% drawdown."},
    ]
    results: list[dict[str, Any]] = []
    for scenario in scenarios:
        metrics = _simulate_closed_trades(closes, starting_capital=starting_capital, **scenario["params"])
        ending_multiple = _coerce_float(metrics.get("ending_multiple"), 1.0)
        results.append({
            **scenario,
            "metrics": metrics,
            "pass_checks": {
                "reaches_target_multiple": ending_multiple >= target_multiple,
                "max_drawdown_under_30pct": _coerce_float(metrics.get("max_drawdown_pct"), 0.0) <= 30.0,
                "account_not_ruined": _coerce_float(metrics.get("ending_equity"), starting_capital) > 0.0,
            },
        })
    return results


def summarize_10x_validation(*, reference: TenXReference, ledger: pd.DataFrame, ledger_source: dict[str, Any]) -> dict[str, Any]:
    reference_analysis = analyze_reference_path(reference)
    equity_series = _fallback_equity_from_ledger(ledger, starting_capital=reference.starting_capital)
    local_metrics = _equity_metrics(equity_series, starting_capital=reference.starting_capital)
    scenario_matrix = build_10x_scenario_matrix(ledger, starting_capital=reference.starting_capital, target_multiple=reference.target_multiple)
    closed_count = int((_closed_trade_frame(ledger)).shape[0])
    stress_keys = {"ten_x_cost_check_2x_slippage", "ten_x_cost_check_3x_slippage", "ten_x_next_bar_10bps", "ten_x_next_bar_25bps"}
    stress_available = not ledger.empty and closed_count > 0
    stress_passes = [bool(item["pass_checks"]["reaches_target_multiple"]) for item in scenario_matrix if item["key"] in stress_keys] if stress_available else []
    if not reference_analysis["pass_checks"]["reached_10x_equity_target"]:
        verdict = "not_passed"
    elif not stress_available:
        verdict = "reference_passed_local_ledger_needed"
    elif all(stress_passes):
        verdict = "passed_10x_stand_test"
    elif any(stress_passes):
        verdict = "partial_pass_high_execution_sensitivity"
    else:
        verdict = "failed_under_execution_stress"
    return {
        "generated_at": pd.Timestamp.utcnow().isoformat(),
        "goal": "Validate whether the current strategy result can stand as a 10x account-value result, not activate a live 10x deployment.",
        "verdict": verdict,
        "reference_analysis": reference_analysis,
        "local_ledger": {
            "source": ledger_source,
            "row_count": int(len(ledger.index)),
            "closed_trade_count": closed_count,
            "metrics": local_metrics,
            "has_mark_to_market_intratrade_equity": False,
        },
        "scenario_matrix": scenario_matrix,
        "pass_fail_rule": {
            "required": [
                "reference current equity at or above 10x starting capital",
                "local ledger available with closed trades",
                "2x slippage scenario still reaches 10x",
                "next-bar 10 bps penalty scenario still reaches 10x",
                "max drawdown remains under 30% for aggressive paper test",
            ],
            "live_deployment_gate": "Do not use the aggressive profile live. Use it only for paper validation. Live version should be separately capped.",
        },
    }


def _format_money(value: Any) -> str:
    return f"${_coerce_float(value, 0.0):,.2f}"


def _format_pct(value: Any) -> str:
    return f"{_coerce_float(value, 0.0):.2f}%"


def render_report(summary: dict[str, Any]) -> str:
    reference = summary["reference_analysis"]
    local_ledger = summary["local_ledger"]
    lines = [
        "# 10x Stand Test Report", "", "## Purpose", "",
        "This report tests whether the current strategy result can stand as a 10x account-value result. It does not treat 10x gross leverage as safe for live trading.", "",
        "## Verdict", "",
        f"- Verdict: `{summary['verdict']}`",
        f"- Starting capital: {_format_money(reference['starting_capital'])}",
        f"- Target equity: {_format_money(reference['target_equity'])}",
        f"- Current reference equity: {_format_money(reference['current_equity'])}",
        f"- Current reference multiple: {reference['current_multiple']:.2f}x",
        f"- Known drawdown: {_format_money(reference['known_drawdown_dollars'])}",
        f"- Known max drawdown: {_format_pct(reference['known_max_drawdown_pct'])}",
        f"- Drawdown versus original starting capital: {reference['known_drawdown_vs_start_multiple']:.2f}x", "",
        "## Local ledger status", "",
        f"- Source: `{local_ledger['source'].get('source')}`",
        f"- Rows: {local_ledger['row_count']}",
        f"- Closed trades: {local_ledger['closed_trade_count']}",
        f"- Local ending equity: {_format_money(local_ledger['metrics']['ending_equity'])}",
        f"- Local ending multiple: {local_ledger['metrics']['ending_multiple']:.2f}x",
        f"- Local max drawdown: {_format_pct(local_ledger['metrics']['max_drawdown_pct'])}", "",
        "## Scenario matrix", "",
        "| Scenario | Ending multiple | Max drawdown | Reaches 10x | Notes |",
        "|---|---:|---:|---:|---|",
    ]
    for item in summary["scenario_matrix"]:
        metrics = item["metrics"]
        reaches = "yes" if item["pass_checks"]["reaches_target_multiple"] else "no"
        lines.append(f"| {item['label']} | {_coerce_float(metrics.get('ending_multiple'), 1.0):.2f}x | {_format_pct(metrics.get('max_drawdown_pct'))} | {reaches} | {item['purpose']} |")
    lines.extend(["", "## How to read this", "", "The reference result has already cleared the 10x account-value target if current equity is at or above $1,000,000 on a $100,000 start.", "The local ledger and scenario matrix test whether that result survives execution costs, delayed fills, and notional caps.", "If the report says `reference_passed_local_ledger_needed`, the known equity path reached 10x, but the local database or ledger was not available in this cleaned package."])
    return "\n".join(lines) + "\n"


def export_ten_x_stand_test(
    *,
    tenant_slug: str = "systematic-equities",
    starting_capital: float = 100000.0,
    current_equity: float = 1900000.0,
    target_multiple: float = 10.0,
    known_peak_equity: float = 1900000.0,
    known_drawdown_peak: float = 1800000.0,
    known_drawdown_trough: float = 1300000.0,
    known_max_drawdown_pct: float = 27.8,
    ledger_csv: Path | None = None,
    output_dir: Path | None = None,
) -> TenXExportResult:
    destination = output_dir or (TEN_X_EXPORT_DIR / "latest")
    destination.mkdir(parents=True, exist_ok=True)
    reference = TenXReference(
        starting_capital=starting_capital,
        current_equity=current_equity,
        target_multiple=target_multiple,
        known_peak_equity=known_peak_equity,
        known_drawdown_peak=known_drawdown_peak,
        known_drawdown_trough=known_drawdown_trough,
        known_max_drawdown_pct=known_max_drawdown_pct,
    )
    ledger, ledger_source = _load_local_ledger(tenant_slug=tenant_slug, starting_capital=starting_capital, ledger_csv=ledger_csv)
    summary = summarize_10x_validation(reference=reference, ledger=ledger, ledger_source=ledger_source)
    summary_path = destination / "ten_x_stand_test_summary.json"
    scenario_matrix_path = destination / "ten_x_scenario_matrix.json"
    report_path = destination / "TEN_X_STAND_TEST_REPORT.md"
    ledger_path: Path | None = None
    summary_path.write_text(json.dumps(_safe_json(summary), indent=2), encoding="utf-8")
    scenario_matrix_path.write_text(json.dumps(_safe_json(summary["scenario_matrix"]), indent=2), encoding="utf-8")
    report_path.write_text(render_report(summary), encoding="utf-8")
    if not ledger.empty:
        ledger_path = destination / "ten_x_trade_validation_ledger.csv"
        ledger.to_csv(ledger_path, index=False)
    return TenXExportResult(destination, report_path, summary_path, scenario_matrix_path, ledger_path, summary)
