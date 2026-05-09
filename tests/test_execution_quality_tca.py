from __future__ import annotations

import inspect
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from backend.api import create_app
from backend.services import execution_quality_tca as tca
from backend.services.execution_quality_tca import (
    build_execution_quality_tca_report,
    compute_alpha_decay,
    compute_fill_delay_seconds,
    compute_slippage_bps,
    compute_spread_cost_bps,
    normalize_execution_quality_record,
)


def _row(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "order_id": "order-1",
        "trade_id": "trade-1",
        "linked_candidate_id": "candidate-1",
        "symbol": "AAPL",
        "timestamp": "2026-05-06T14:00:00Z",
        "submitted_at": "2026-05-06T14:00:00Z",
        "filled_at": "2026-05-06T14:00:06Z",
        "engine": "intraday_momentum",
        "setup_type": "vwap_reclaim",
        "regime": "trend_day",
        "route": "broker_paper",
        "expected_entry_price": 100.0,
        "actual_fill_price": 100.25,
        "spread_at_signal": 8.0,
        "total_reward": 0.60,
        "actual_forward_return": 0.70,
        "baseline_forward_return": 0.10,
        "alpha_at_signal": 0.80,
        "alpha_after_fill": 0.55,
        "quote_age_seconds": 4.0,
        "liquidity_score": 0.80,
    }
    payload.update(overrides)
    return payload


class ExecutionQualityTcaTests(unittest.TestCase):
    def test_slippage_calculation(self) -> None:
        self.assertAlmostEqual(compute_slippage_bps(100.0, 100.25), 25.0)
        self.assertAlmostEqual(compute_slippage_bps(100.0, 100.25, 12.5), 12.5)

    def test_spread_cost_calculation(self) -> None:
        self.assertEqual(compute_spread_cost_bps(8), 8.0)
        self.assertEqual(compute_spread_cost_bps(-2), 0.0)

    def test_fill_delay_calculation(self) -> None:
        self.assertEqual(compute_fill_delay_seconds(_row()), 6.0)
        self.assertEqual(compute_fill_delay_seconds(_row(latency_ms=2500, filled_at=None)), 2.5)

    def test_alpha_decay_calculation(self) -> None:
        self.assertAlmostEqual(compute_alpha_decay(_row()), 0.25)
        self.assertAlmostEqual(compute_alpha_decay(_row(alpha_decay=0.12)), 0.12)

    def test_execution_adjusted_reward_and_cost_edge(self) -> None:
        record = normalize_execution_quality_record(_row(), 0)

        self.assertIsNotNone(record)
        self.assertAlmostEqual(record["slippage"], 25.0)
        self.assertAlmostEqual(record["execution_adjusted_reward"], 0.27)
        self.assertAlmostEqual(record["cost_adjusted_edge"], 0.27)
        self.assertTrue(record["paper_only"])

    def test_missing_fill_fields(self) -> None:
        record = normalize_execution_quality_record(_row(actual_fill_price=None, filled_at=None), 0)

        self.assertIn("fill_price", record["missing_fields"])
        self.assertIn("slippage", record["missing_fields"])
        self.assertTrue(record["warnings"])

    def test_partial_and_missed_fill_aggregation(self) -> None:
        report = build_execution_quality_tca_report(
            records=[
                _row(order_id="filled"),
                _row(order_id="partial", filled_quantity=5, quantity=10, status="partially_filled"),
                _row(order_id="missed", actual_fill_price=None, status="rejected"),
            ],
            generated_at="2026-05-06T00:00:00Z",
        )

        self.assertAlmostEqual(report["summary"]["partial_fill_rate"], 1 / 3, places=6)
        self.assertAlmostEqual(report["summary"]["missed_fill_rate"], 1 / 3, places=6)
        self.assertTrue(report["research_only"])
        self.assertTrue(report["paper_only"])

    def test_live_route_rows_are_excluded(self) -> None:
        report = build_execution_quality_tca_report(
            records=[_row(order_id="paper", route="broker_paper"), _row(order_id="live", route="broker_live")],
            generated_at="2026-05-06T00:00:00Z",
        )

        self.assertEqual(report["summary"]["trade_count"], 1)
        self.assertEqual(report["records"][0]["order_id"], "paper")

    def test_api_response_shape(self) -> None:
        client = TestClient(create_app())
        with patch.object(tca, "execution_quality_summary", lambda db=None, current_user=None: {"rows": [_row()]}), patch.object(
            tca,
            "get_evidence_reward_summary",
            lambda db=None, current_user=None: {"records": []},
        ):
            for path in (
                "/api/execution-quality/summary",
                "/api/execution-quality/trades",
                "/api/execution-quality/slippage",
                "/api/execution-quality/alpha-decay",
                "/api/execution-quality/engines",
                "/api/execution-quality/setups",
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
                self.assertIn("Does not change order routing.", data["safety_notes"])

    def test_service_contains_no_routing_execution_broker_or_ranking_mutation_calls(self) -> None:
        source = inspect.getsource(tca)
        forbidden_calls = (
            "place_order(",
            "submit_order(",
            "clear_kill_switch(",
            "set_broker_route(",
            "update_ranking_weight(",
            "update_risk_config(",
            "enable_live_trading(",
            "route_order(",
        )
        for call in forbidden_calls:
            self.assertNotIn(call, source)


if __name__ == "__main__":
    unittest.main()
