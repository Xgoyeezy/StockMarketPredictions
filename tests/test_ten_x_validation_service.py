from __future__ import annotations

import unittest

import pandas as pd

from backend.services.ten_x_validation_service import (
    TenXReference,
    analyze_reference_path,
    build_10x_scenario_matrix,
    summarize_10x_validation,
)


class TenXValidationServiceTests(unittest.TestCase):
    def test_reference_path_identifies_reached_10x_high_volatility(self) -> None:
        analysis = analyze_reference_path(
            TenXReference(
                starting_capital=100000.0,
                current_equity=1900000.0,
                target_multiple=10.0,
                known_peak_equity=1900000.0,
                known_drawdown_peak=1800000.0,
                known_drawdown_trough=1300000.0,
                known_max_drawdown_pct=27.8,
            )
        )
        self.assertTrue(analysis["pass_checks"]["reached_10x_equity_target"])
        self.assertTrue(analysis["pass_checks"]["known_drawdown_under_30pct"])
        self.assertFalse(analysis["pass_checks"]["known_drawdown_under_20pct"])
        self.assertEqual(analysis["state"], "10x_reached_high_volatility")

    def test_scenario_matrix_flags_10x_after_costs(self) -> None:
        ledger = pd.DataFrame(
            [
                {"event_type": "close", "timestamp": "2026-01-01T15:00:00Z", "realized_pnl": 450000.0, "position_cost": 100000.0, "slippage": 0.0, "fees": 0.0, "equity_after_fill": 550000.0},
                {"event_type": "close", "timestamp": "2026-01-02T15:00:00Z", "realized_pnl": 550000.0, "position_cost": 100000.0, "slippage": 0.0, "fees": 0.0, "equity_after_fill": 1100000.0},
            ]
        )
        matrix = build_10x_scenario_matrix(ledger, starting_capital=100000.0, target_multiple=10.0)
        baseline = next(item for item in matrix if item["key"] == "v0_local_records")
        self.assertTrue(baseline["pass_checks"]["reaches_target_multiple"])
        self.assertGreaterEqual(baseline["metrics"]["ending_multiple"], 10.0)

    def test_summary_requires_local_ledger_for_full_pass(self) -> None:
        summary = summarize_10x_validation(
            reference=TenXReference(starting_capital=100000.0, current_equity=1900000.0),
            ledger=pd.DataFrame(),
            ledger_source={"source": "test"},
        )
        self.assertEqual(summary["verdict"], "reference_passed_local_ledger_needed")


if __name__ == "__main__":
    unittest.main()
