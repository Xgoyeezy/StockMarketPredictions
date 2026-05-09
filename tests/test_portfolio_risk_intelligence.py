from __future__ import annotations

import inspect
import unittest

from fastapi.testclient import TestClient

from backend.api import create_app
from backend.services import portfolio_risk_intelligence as pri
from backend.services.portfolio_risk_intelligence import (
    build_portfolio_risk_report,
    compute_correlation_heat,
    compute_position_notional,
    normalize_portfolio_risk_record,
)


def _row(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "record_id": "pos-1",
        "symbol": "AAPL",
        "timestamp": "2026-05-06T14:00:00Z",
        "engine": "intraday_momentum",
        "setup_type": "vwap_reclaim",
        "strategy": "momentum_core",
        "regime": "trend_day",
        "sector": "technology",
        "route": "broker_paper",
        "side": "long",
        "notional": 10000.0,
        "max_risk_dollars": 100.0,
        "beta_to_SPY": 1.2,
        "beta_to_QQQ": 1.1,
        "liquidity_score": 0.82,
        "spread_bps": 8.0,
        "forecast_confidence": 0.74,
        "account_size": 100000.0,
    }
    payload.update(overrides)
    return payload


class PortfolioRiskIntelligenceTests(unittest.TestCase):
    def test_gross_net_long_and_proxy_exposure(self) -> None:
        report = build_portfolio_risk_report(
            records=[
                _row(record_id="aapl", symbol="AAPL", notional=10000.0, beta_to_SPY=1.2),
                _row(record_id="msft", symbol="MSFT", notional=5000.0, beta_to_SPY=1.1),
                _row(record_id="sh", symbol="SH", sector="inverse_proxy", notional=20000.0, beta_to_SPY=1.0),
            ],
            generated_at="2026-05-06T00:00:00Z",
        )

        self.assertEqual(report["summary"]["gross_exposure"], 35000.0)
        self.assertEqual(report["summary"]["long_exposure"], 15000.0)
        self.assertEqual(report["summary"]["short_or_proxy_exposure"], 20000.0)
        self.assertEqual(report["summary"]["net_exposure"], -5000.0)
        self.assertTrue(report["research_only"])
        self.assertTrue(report["paper_only"])

    def test_position_notional_from_units_and_price(self) -> None:
        self.assertEqual(compute_position_notional({"quantity": 20, "current_price": 50}), 1000.0)
        self.assertEqual(compute_position_notional({"instrument_type": "listed_option", "quantity": 2, "current_price": 1.5}), 300.0)

    def test_sector_engine_and_setup_exposure(self) -> None:
        report = build_portfolio_risk_report(
            records=[
                _row(symbol="AAPL", sector="technology", engine="macro", setup_type="risk_on", notional=12000),
                _row(symbol="JPM", sector="financials", engine="stat_arb", setup_type="mean_reversion", notional=8000),
            ],
            generated_at="2026-05-06T00:00:00Z",
        )

        sectors = {row["sector"]: row["gross_exposure"] for row in report["aggregations"]["sector_exposure"]}
        engines = {row["engine"]: row["gross_exposure"] for row in report["aggregations"]["engine_exposure"]}
        setups = {row["setup_type"]: row["gross_exposure"] for row in report["aggregations"]["setup_exposure"]}
        self.assertEqual(sectors["technology"], 12000)
        self.assertEqual(sectors["financials"], 8000)
        self.assertEqual(engines["macro"], 12000)
        self.assertEqual(setups["mean_reversion"], 8000)

    def test_symbol_concentration(self) -> None:
        report = build_portfolio_risk_report(
            records=[_row(symbol="AAPL", notional=10000), _row(symbol="MSFT", notional=30000)],
            generated_at="2026-05-06T00:00:00Z",
        )

        self.assertAlmostEqual(report["summary"]["symbol_concentration"], 0.75)

    def test_correlation_heat_with_fixture_data(self) -> None:
        records = [
            normalize_portfolio_risk_record(_row(symbol="AAPL", notional=10000), 0),
            normalize_portfolio_risk_record(_row(symbol="MSFT", notional=10000), 1),
            normalize_portfolio_risk_record(_row(symbol="JPM", sector="financials", notional=5000), 2),
        ]
        heat = compute_correlation_heat([row for row in records if row])

        self.assertEqual(heat["max_bucket_share"], 0.8)
        self.assertEqual(heat["correlation_heat"], 80.0)

    def test_stress_test_calculations(self) -> None:
        report = build_portfolio_risk_report(
            records=[
                _row(symbol="AAPL", notional=10000.0, beta_to_SPY=1.2),
                _row(symbol="MSFT", notional=5000.0, beta_to_SPY=1.1),
                _row(symbol="SH", sector="inverse_proxy", notional=20000.0, beta_to_SPY=1.0),
            ],
            generated_at="2026-05-06T00:00:00Z",
        )
        stress = {row["scenario"]: row for row in report["stress_tests"]}

        self.assertAlmostEqual(stress["market_down_2_percent"]["estimated_pnl"], 50.0)
        self.assertIn("broker_outage", stress)

    def test_missing_sector_and_beta_data_are_reported(self) -> None:
        report = build_portfolio_risk_report(
            records=[_row(symbol="ZZZZ", sector="", beta_to_SPY=None, beta_to_QQQ=None, liquidity_score=None)],
            generated_at="2026-05-06T00:00:00Z",
        )

        self.assertGreaterEqual(report["missing_fields"].get("sector", 0), 1)
        self.assertGreaterEqual(report["missing_fields"].get("beta_to_SPY", 0), 1)
        self.assertGreaterEqual(report["missing_fields"].get("beta_to_QQQ", 0), 1)
        self.assertTrue(report["warnings"])

    def test_api_response_shape(self) -> None:
        client = TestClient(create_app())
        original_loader = pri._load_runtime_rows
        pri._load_runtime_rows = lambda db=None, current_user=None: ([_row()], [])
        try:
            for path in (
                "/api/portfolio-risk/summary",
                "/api/portfolio-risk/exposures",
                "/api/portfolio-risk/concentration",
                "/api/portfolio-risk/correlation",
                "/api/portfolio-risk/stress-tests",
                "/api/portfolio-risk/regimes",
            ):
                response = client.get(path)
                self.assertEqual(response.status_code, 200)
                payload = response.json()
                self.assertTrue(payload["ok"])
                data = payload["data"]
                self.assertTrue(data["research_only"])
                self.assertTrue(data["paper_only"])
                self.assertFalse(data["can_submit_orders"])
                self.assertFalse(data["can_submit_live_orders"])
                self.assertIn("summary", data)
                self.assertIn("records", data)
                self.assertIn("aggregations", data)
                self.assertIn("warnings", data)
                self.assertIn("missing_fields", data)
                self.assertIn("safety_notes", data)
                self.assertIn("Risk visibility only. Does not enforce, loosen, or change risk gates.", data["safety_notes"])
        finally:
            pri._load_runtime_rows = original_loader

    def test_service_contains_no_risk_execution_broker_or_ranking_mutation_calls(self) -> None:
        source = inspect.getsource(pri)
        forbidden_calls = (
            "place_order(",
            "submit_order(",
            "clear_kill_switch(",
            "set_broker_route(",
            "update_ranking_weight(",
            "update_risk_config(",
            "set_risk_kill_switch(",
            "enable_live_trading(",
            "route_order(",
        )
        for call in forbidden_calls:
            self.assertNotIn(call, source)


if __name__ == "__main__":
    unittest.main()
