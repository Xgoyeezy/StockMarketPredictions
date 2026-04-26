from __future__ import annotations

from collections import defaultdict
from typing import Any

from backend.services.strategy_engine.types import DeskTargetProposal, PortfolioTarget


DEFAULT_ALLOCATOR_CONFIG: dict[str, float] = {
    "capital_base": 100000.0,
    "max_gross_exposure": 1.5,
    "max_net_exposure": 1.0,
    "max_symbol_weight": 0.25,
}


def build_risk_state(allocator_config: dict[str, Any] | None = None) -> dict[str, Any]:
    config = dict(DEFAULT_ALLOCATOR_CONFIG)
    config.update(dict(allocator_config or {}))
    return {
        "capital_base": float(config.get("capital_base") or 100000.0),
        "max_gross_exposure": float(config.get("max_gross_exposure") or 1.5),
        "max_net_exposure": float(config.get("max_net_exposure") or 1.0),
        "max_single_position_pct": float(config.get("max_symbol_weight") or 0.25) * 100.0,
    }


def allocate_portfolio_targets(
    desk_targets: list[DeskTargetProposal],
    *,
    allocator_config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    config = dict(DEFAULT_ALLOCATOR_CONFIG)
    config.update(dict(allocator_config or {}))
    capital_base = float(config.get("capital_base") or 100000.0)
    max_gross = float(config.get("max_gross_exposure") or 1.5)
    max_net = float(config.get("max_net_exposure") or 1.0)
    max_symbol_weight = float(config.get("max_symbol_weight") or 0.25)

    symbol_rows: dict[str, list[DeskTargetProposal]] = defaultdict(list)
    for proposal in desk_targets:
        symbol_rows[str(proposal.symbol).upper()].append(proposal)

    portfolio_targets: list[PortfolioTarget] = []
    gross_exposure = 0.0
    net_exposure = 0.0
    for symbol, proposals in sorted(symbol_rows.items()):
        target_weight = sum(float(item.target_weight) for item in proposals)
        raw_notional = capital_base * target_weight
        clipped_weight = max(-max_symbol_weight, min(max_symbol_weight, target_weight))
        clipped_notional = capital_base * clipped_weight
        gross_exposure += abs(clipped_weight)
        net_exposure += clipped_weight
        directions = tuple(sorted({str(item.direction) for item in proposals}))
        portfolio_targets.append(
            PortfolioTarget(
                symbol=symbol,
                target_weight=round(clipped_weight, 6),
                target_notional=round(clipped_notional, 2),
                directions=directions,
                desk_contributions=tuple(
                    {
                        "desk_key": item.desk_key,
                        "target_weight": item.target_weight,
                        "target_notional": item.target_notional,
                        "direction": item.direction,
                        "confidence_score": item.confidence_score,
                    }
                    for item in proposals
                ),
                risk_flags=tuple(
                    flag
                    for flag in (
                        "symbol_weight_clipped" if round(clipped_weight, 6) != round(target_weight, 6) else None,
                        "raw_notional_changed" if round(clipped_notional, 2) != round(raw_notional, 2) else None,
                    )
                    if flag
                ),
                order_plan={
                    "side": "buy" if clipped_weight > 0 else "sell_short" if clipped_weight < 0 else "hold",
                    "notional": round(abs(clipped_notional), 2),
                    "order_type": "limit",
                    "time_in_force": "day",
                },
            )
        )

    gross_ok = gross_exposure <= max_gross
    net_ok = abs(net_exposure) <= max_net
    return {
        "targets": [target.to_dict() for target in portfolio_targets],
        "metrics": {
            "gross_exposure": round(gross_exposure, 6),
            "net_exposure": round(net_exposure, 6),
            "target_count": len(portfolio_targets),
            "desk_count": len({target.desk_key for target in desk_targets}),
            "capital_base": capital_base,
        },
        "risk": {
            "allowed": bool(gross_ok and net_ok),
            "gross_ok": gross_ok,
            "net_ok": net_ok,
            "max_gross_exposure": max_gross,
            "max_net_exposure": max_net,
        },
    }
