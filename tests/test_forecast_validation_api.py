from __future__ import annotations

import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from backend.api import create_app


class ForecastValidationApiTests(unittest.TestCase):
    def test_read_only_forecast_validation_endpoints_return_envelopes(self) -> None:
        client = TestClient(create_app())
        for path in (
            "/api/forecast-validation/summary",
            "/api/forecast-validation/predictions",
            "/api/forecast-validation/models",
            "/api/forecast-validation/regimes",
        ):
            response = client.get(path)
            self.assertEqual(response.status_code, 200)
            payload = response.json()
            self.assertTrue(payload["ok"])
            self.assertIn("data", payload)
            self.assertEqual(payload["data"]["mode"], "research_only")
            self.assertTrue(payload["data"]["research_only"])
            self.assertIn("safety_notes", payload["data"])
            self.assertIn("summary", payload["data"])

    def test_summary_declares_execution_safety_boundary(self) -> None:
        client = TestClient(create_app())
        response = client.get("/api/forecast-validation/summary")

        self.assertEqual(response.status_code, 200)
        data = response.json()["data"]
        self.assertIn("never adjusts execution", data["safety"])
        self.assertIn("Does not place orders.", data["safety_notes"])
        self.assertEqual(
            data["reward_formula"],
            "direction_score + path_fit_score + timing_score - drawdown_penalty - volatility_mismatch_penalty - confidence_penalty",
        )

    def test_forecast_validation_service_has_no_execution_mutation_calls(self) -> None:
        source = Path("backend/services/forecast_validation_engine.py").read_text(encoding="utf-8")

        forbidden_calls = (
            "submit_order(",
            "place_order(",
            "clear_kill_switch(",
            "set_broker_route(",
            "update_ranking_weight(",
            "enable_live_trading(",
        )
        for call in forbidden_calls:
            self.assertNotIn(call, source)


if __name__ == "__main__":
    unittest.main()
