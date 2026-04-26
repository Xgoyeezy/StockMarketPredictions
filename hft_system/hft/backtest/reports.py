from __future__ import annotations

import json
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any

from hft.monitoring.dashboard import build_dashboard_payload

try:
    import plotly.graph_objects as go
except Exception:  # pragma: no cover
    go = None


def _serialize(value: Any) -> Any:
    if is_dataclass(value):
        return asdict(value)
    if isinstance(value, list):
        return [_serialize(item) for item in value]
    if isinstance(value, dict):
        return {key: _serialize(item) for key, item in value.items()}
    return value


def write_json_report(path: str | Path, payload: dict[str, Any]) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(_serialize(payload), indent=2, sort_keys=True), encoding="utf-8")
    return path


def write_html_report(path: str | Path, result: Any) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    dashboard = build_dashboard_payload(result)
    inventory = dashboard.get("inventory") or {}
    metrics = dashboard.get("metrics") or {}
    analysis_sections = _serialize(getattr(result, "analysis_sections", {}) or {})
    bridge_export = build_read_only_bridge(result)

    if go is not None and getattr(result, "equity_curve", None):
        figure = go.Figure()
        figure.add_trace(go.Scatter(y=result.equity_curve, mode="lines", name="Equity"))
        chart_html = figure.to_html(full_html=False, include_plotlyjs="cdn")
    else:
        chart_html = "<p>Plotly unavailable or no equity curve.</p>"

    html = f"""
    <html>
      <head><title>HFT Replay Report</title></head>
      <body>
        <h1>HFT Replay Report</h1>
        <h2>Run {dashboard.get('run_id')}</h2>
        <p>Symbol: {dashboard.get('symbol')}</p>
        <p>Orders: {dashboard.get('orders_submitted')} | Fills: {dashboard.get('fills')}</p>
        <h3>Metrics</h3>
        <pre>{json.dumps(metrics, indent=2, sort_keys=True)}</pre>
        <h3>Inventory</h3>
        <pre>{json.dumps(inventory, indent=2, sort_keys=True)}</pre>
        <h3>Analysis</h3>
        <pre>{json.dumps(analysis_sections, indent=2, sort_keys=True)}</pre>
        <h3>Read-only bridge</h3>
        <pre>{json.dumps(bridge_export, indent=2, sort_keys=True)}</pre>
        <h3>Chart</h3>
        {chart_html}
      </body>
    </html>
    """
    path.write_text(html, encoding="utf-8")
    return path


def build_read_only_bridge(result: Any) -> dict[str, Any]:
    attribution = getattr(result, "attribution_snapshot", None)
    inventory = getattr(result, "inventory_snapshot", None)
    strategy_health = getattr(result, "strategy_health", None)
    return {
        "simulated_pnl": {
            "realized_pnl": getattr(attribution, "realized_pnl", 0.0),
            "unrealized_pnl": getattr(attribution, "unrealized_pnl", 0.0),
            "spread_capture": getattr(attribution, "spread_capture", 0.0),
            "fees": getattr(attribution, "fees", 0.0),
            "rebates": getattr(attribution, "rebates", 0.0),
            "inventory_pnl": getattr(attribution, "inventory_pnl", 0.0),
            "adverse_selection": getattr(attribution, "adverse_selection", 0.0),
            "slippage": getattr(attribution, "slippage", 0.0),
        },
        "inventory_summary": _serialize(inventory) if inventory else {},
        "risk_summary": {
            "alerts": [_serialize(item) for item in getattr(result, "alerts", [])],
            "message_rate": getattr(result, "metrics", {}).get("message_rate", 0.0),
            "fill_rate": getattr(result, "metrics", {}).get("fill_rate", 0.0),
        },
        "strategy_health_metrics": _serialize(strategy_health) if strategy_health else {},
    }
