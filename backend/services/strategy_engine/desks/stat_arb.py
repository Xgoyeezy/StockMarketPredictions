from __future__ import annotations

from typing import Any

import pandas as pd

from backend.services.feature_store.service import compute_realized_vol
from backend.services.strategy_engine.base import StrategyDesk
from backend.services.strategy_engine.types import (
    DeskDataRequirement,
    DeskFeatureFrame,
    DeskSignal,
    DeskTargetProposal,
    DeskValidationResult,
    utc_now_iso,
)

_DEFAULT_PAIRS = (("SPY", "QQQ"), ("XLE", "XOP"), ("SMH", "SOXX"), ("XLF", "KBE"))


class StatisticalArbitrageDesk(StrategyDesk):
    desk_key = "stat_arb"
    display_name = "Statistical Arbitrage Desk"
    lifecycle_stage = "paper"
    trading_mode = "paper"
    paper_trading_enabled = True

    def get_data_requirements(self) -> tuple[DeskDataRequirement, ...]:
        pairs = self.config.get("pairs") or _DEFAULT_PAIRS
        tickers = {str(item).strip().upper() for pair in pairs for item in pair if str(item).strip()}
        return (
            DeskDataRequirement(
                family="bars",
                tickers=tuple(sorted(tickers)),
                period=str(self.config.get("period") or "1y"),
                interval=str(self.config.get("interval") or "1d"),
                prepost=self.include_extended_hours(),
                notes="Stat arb close history for hedge ratio and spread z-score estimation with pre/post-market bars enabled.",
            ),
        )

    def compute_features(self, market_state: dict[str, Any]) -> DeskFeatureFrame:
        bars = dict(market_state.get("bars") or {})
        rows: list[dict[str, Any]] = []
        lookback = int(self.config.get("spread_lookback") or 60)
        z_window = int(self.config.get("z_window") or 20)
        for left_symbol, right_symbol in self.config.get("pairs") or _DEFAULT_PAIRS:
            left_frame = bars.get(str(left_symbol).upper(), pd.DataFrame())
            right_frame = bars.get(str(right_symbol).upper(), pd.DataFrame())
            if left_frame.empty or right_frame.empty:
                continue
            combined = pd.concat(
                [
                    pd.to_numeric(left_frame["Close"], errors="coerce").rename("left"),
                    pd.to_numeric(right_frame["Close"], errors="coerce").rename("right"),
                ],
                axis=1,
                join="inner",
            ).dropna()
            if len(combined.index) < max(lookback, z_window) + 5:
                continue
            sample = combined.tail(lookback)
            right_variance = float(sample["right"].var(ddof=0))
            if right_variance <= 0:
                continue
            hedge_ratio = float(sample["left"].cov(sample["right"]) / right_variance)
            spread = sample["left"] - hedge_ratio * sample["right"]
            spread_mean = float(spread.tail(z_window).mean())
            spread_std = float(spread.tail(z_window).std(ddof=0))
            latest_spread = float(spread.iloc[-1])
            z_score = (latest_spread - spread_mean) / spread_std if spread_std > 0 else 0.0
            correlation = float(sample["left"].corr(sample["right"]))
            rows.append(
                {
                    "pair": f"{left_symbol}/{right_symbol}",
                    "left_symbol": str(left_symbol).upper(),
                    "right_symbol": str(right_symbol).upper(),
                    "hedge_ratio": hedge_ratio,
                    "spread_mean": spread_mean,
                    "spread_std": spread_std,
                    "latest_spread": latest_spread,
                    "z_score": float(z_score),
                    "correlation": correlation,
                    "spread_volatility": compute_realized_vol(sample.rename(columns={"left": "Close"}), 20),
                }
            )
        rows.sort(key=lambda item: abs(float(item.get("z_score") or 0.0)), reverse=True)
        return DeskFeatureFrame(
            desk_key=self.desk_key,
            as_of=market_state.get("as_of") or utc_now_iso(),
            feature_rows=tuple(rows),
            summary={"pair_count": len(rows), "entry_threshold": float(self.config.get("entry_z") or 1.5)},
        )

    def generate_signal(self, features: DeskFeatureFrame) -> DeskSignal:
        entry_z = float(self.config.get("entry_z") or 1.5)
        candidates = [row for row in features.feature_rows if abs(float(row.get("z_score") or 0.0)) >= entry_z]
        top_candidates = candidates[: int(self.config.get("max_pairs") or 2)]
        average_z = sum(abs(float(row["z_score"])) for row in top_candidates) / max(len(top_candidates), 1)
        confidence = min(0.95, 0.45 + average_z / 4.0) if top_candidates else 0.0
        avg_spread_vol = sum(float(row.get("spread_volatility") or 0.0) for row in top_candidates) / max(len(top_candidates), 1)
        return DeskSignal(
            desk_key=self.desk_key,
            generated_at=utc_now_iso(),
            signal_type="mean_reversion",
            summary="Stat arb signal based on hedge-adjusted spread dislocations.",
            confidence_score=round(confidence, 4),
            expected_holding_period="5d",
            risk_estimate=round(avg_spread_vol, 4),
            required_capital=float(self.config.get("capital_base") or 100000.0) * min(len(top_candidates), 2) / 2.0,
            components={
                "entry_threshold": entry_z,
                "pair_candidates": [dict(row) for row in top_candidates],
            },
            metadata={"feature_as_of": features.as_of},
        )

    def generate_target_positions(self, signal: DeskSignal) -> tuple[DeskTargetProposal, ...]:
        candidates = list(signal.components.get("pair_candidates") or [])
        if not candidates:
            return ()
        capital_base = float(self.config.get("capital_base") or 100000.0)
        pair_budget = capital_base / max(len(candidates), 1)
        targets: list[DeskTargetProposal] = []
        for row in candidates:
            left_symbol = str(row["left_symbol"]).upper()
            right_symbol = str(row["right_symbol"]).upper()
            z_score = float(row["z_score"])
            hedge_ratio = max(abs(float(row.get("hedge_ratio") or 1.0)), 0.25)
            first_direction = "short" if z_score > 0 else "long"
            second_direction = "long" if z_score > 0 else "short"
            left_weight = 0.5
            right_weight = min(0.5 * hedge_ratio, 0.75)
            targets.append(
                DeskTargetProposal(
                    desk_key=self.desk_key,
                    symbol=left_symbol,
                    direction=first_direction,
                    target_weight=round(left_weight, 6) if first_direction == "long" else round(-left_weight, 6),
                    target_notional=round(pair_budget * left_weight, 2),
                    confidence_score=signal.confidence_score,
                    expected_holding_period=signal.expected_holding_period,
                    risk_estimate=signal.risk_estimate,
                    required_capital=round(pair_budget * left_weight, 2),
                    metadata={"pair": row["pair"], "z_score": z_score, "hedge_ratio": hedge_ratio},
                )
            )
            targets.append(
                DeskTargetProposal(
                    desk_key=self.desk_key,
                    symbol=right_symbol,
                    direction=second_direction,
                    target_weight=round(right_weight, 6) if second_direction == "long" else round(-right_weight, 6),
                    target_notional=round(pair_budget * right_weight, 2),
                    confidence_score=signal.confidence_score,
                    expected_holding_period=signal.expected_holding_period,
                    risk_estimate=signal.risk_estimate,
                    required_capital=round(pair_budget * right_weight, 2),
                    metadata={"pair": row["pair"], "z_score": z_score, "hedge_ratio": hedge_ratio},
                )
            )
        return tuple(targets)

    def validate_signal(self, signal: DeskSignal, risk_state: dict[str, Any]) -> DeskValidationResult:
        pair_candidates = list(signal.components.get("pair_candidates") or [])
        max_gross = float(risk_state.get("max_gross_exposure") or 2.0)
        allowed = bool(pair_candidates and signal.confidence_score >= 0.55 and max_gross >= 1.5)
        return DeskValidationResult(
            desk_key=self.desk_key,
            allowed=allowed,
            reason="accepted" if allowed else "insufficient_spread_dislocation",
            detail="Spread dislocations cleared stat-arb entry and gross-exposure gates." if allowed else "Spread dislocations were too small or gross exposure is constrained.",
            metrics={
                "pair_candidate_count": len(pair_candidates),
                "confidence_score": signal.confidence_score,
                "max_gross_exposure": max_gross,
            },
        )
