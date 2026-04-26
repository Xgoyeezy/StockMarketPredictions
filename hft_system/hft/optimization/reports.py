from __future__ import annotations

import json
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any

from hft.optimization.types import ChampionSelectionReport


def _serialize(value: Any) -> Any:
    if is_dataclass(value):
        return asdict(value)
    if isinstance(value, dict):
        return {key: _serialize(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_serialize(item) for item in value]
    return value


def write_optimization_report(path: str | Path, payload: dict[str, Any]) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(_serialize(payload), indent=2, sort_keys=True), encoding="utf-8")
    return path


def write_champion_html_report(
    path: str | Path,
    *,
    champion: ChampionSelectionReport,
    payload: dict[str, Any],
) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    sections = {
        "Baseline vs Champion": payload.get("baseline_vs_champion", {}),
        "Regime Decomposition": payload.get("regime_decomposition", {}),
        "Feature Importance": payload.get("feature_importance", {}),
        "Parameter Sensitivity": payload.get("parameter_sensitivity", {}),
        "Fill Calibration": payload.get("fill_calibration_summary", {}),
        "Inventory Age Analysis": payload.get("inventory_age_analysis", {}),
        "Edge Decay": payload.get("edge_decay", {}),
    }
    html_sections = "\n".join(
        f"<h3>{title}</h3><pre>{json.dumps(_serialize(content), indent=2, sort_keys=True)}</pre>"
        for title, content in sections.items()
    )
    html = f"""
    <html>
      <head><title>HFT Champion Report</title></head>
      <body>
        <h1>Champion Selection Report</h1>
        <p>Accepted: {champion.accepted}</p>
        <p>Reason: {champion.reason}</p>
        <p>Baseline validation score: {champion.baseline_validation_score:.4f}</p>
        <p>Champion validation score: {champion.champion_validation_score:.4f}</p>
        <p>Holdout score: {champion.holdout_score:.4f}</p>
        <p>Holdout degradation: {champion.holdout_degradation:.4f}</p>
        {html_sections}
      </body>
    </html>
    """
    path.write_text(html, encoding="utf-8")
    return path
