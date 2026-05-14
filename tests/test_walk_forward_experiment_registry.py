from __future__ import annotations

import inspect
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from backend.api import create_app
from backend.services import walk_forward_experiment_registry as registry
from backend.services.exceptions import ConflictError, ValidationServiceError
from backend.services.walk_forward_experiment_registry import (
    build_walk_forward_proof_summary,
    clone_walk_forward_experiment,
    create_walk_forward_experiment,
    evaluate_experiment_from_benchmark,
    freeze_walk_forward_experiment,
    get_walk_forward_experiments,
    get_walk_forward_summary,
    update_walk_forward_experiment,
)


def _payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "name": "WF proof run",
        "description": "Freeze evidence rules before forward validation.",
        "train_window": {"start": "2026-05-01", "end": "2026-05-03"},
        "validation_window": {"start": "2026-05-04", "end": "2026-05-04"},
        "test_window": {"start": "2026-05-05", "end": "2026-05-05"},
        "paper_forward_window": {"start": "2026-05-06", "end": "2026-05-10"},
        "strategy_config_version": "strategy_config_v1",
        "risk_config_version": "risk_config_snapshot_v1",
        "ranking_formula_version": "ranked_entry_v1",
        "reward_formula_version": "evidence_reward_prediction_contract_v1",
        "forecast_model_version": "forecast_validation_contract_v1",
        "baseline_definition_version": "professional_benchmark_baselines_v1",
        "feature_version": "candidate_feature_snapshot_v1",
        "market_universe": ["AAPL", "MSFT"],
        "data_source": "local_evidence_artifacts",
    }
    payload.update(overrides)
    return payload


def _benchmark(status: str, *, rewardable: int = 6, candidates: int = 8, edge: float | None = 0.22, lift: float | None = 0.18, quality: float = 85.0) -> dict[str, object]:
    return {
        "status": status,
        "summary": {
            "benchmark_verdict": status,
            "candidate_count": candidates,
            "rewardable_count": rewardable,
            "baseline_relative_edge": edge,
            "score_bucket_lift": lift,
            "data_quality_score": quality,
            "max_drawdown": 0.2,
            "profit_factor": 1.8,
            "verdict_reason": "Fixture benchmark.",
        },
        "sections": {
            "forecast_accuracy": {"direction_accuracy": 0.66},
            "blocker_value": {"items": [{"estimated_blocker_value": 0.12}]},
            "ai_verdict_accuracy": {"verdict_count": 6, "false_positive_rate": 0.1, "false_negative_rate": 0.2},
            "execution_quality": {"slippage_adjusted_reward": 0.31},
        },
        "warnings": [],
        "missing_fields": {},
    }


class WalkForwardExperimentRegistryTests(unittest.TestCase):
    def test_create_draft_experiment(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = Path(tmp) / "experiments.json"
            response = create_walk_forward_experiment(
                _payload(),
                benchmark_report=_benchmark("insufficient_evidence", rewardable=2),
                store_path=store,
                now="2026-05-06T00:00:00Z",
            )

            record = response["record"]
            self.assertEqual(response["status"], "created")
            self.assertEqual(record["status"], "draft")
            self.assertTrue(record["research_only"])
            self.assertFalse(record["can_submit_orders"])
            self.assertEqual(record["mutation"], "research_metadata_only")
            self.assertIn("parameter_digest", record)
            self.assertEqual(record["metrics"]["verdict"], "insufficient_evidence")

    def test_freeze_experiment(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = Path(tmp) / "experiments.json"
            created = create_walk_forward_experiment(_payload(), benchmark_report=_benchmark("edge_detected"), store_path=store)
            experiment_id = created["record"]["experiment_id"]

            frozen = freeze_walk_forward_experiment(experiment_id, store_path=store, now="2026-05-06T01:00:00Z")

            self.assertEqual(frozen["record"]["status"], "frozen")
            self.assertEqual(frozen["record"]["frozen_at"], "2026-05-06T01:00:00Z")
            self.assertIn("clone to make changes", " ".join(frozen["record"]["warnings"]))

    def test_frozen_experiment_cannot_mutate_parameters(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = Path(tmp) / "experiments.json"
            created = create_walk_forward_experiment(_payload(), benchmark_report=_benchmark("edge_detected"), store_path=store)
            experiment_id = created["record"]["experiment_id"]
            freeze_walk_forward_experiment(experiment_id, store_path=store)

            with self.assertRaises(ConflictError):
                update_walk_forward_experiment(experiment_id, {"ranking_formula_version": "changed"}, store_path=store)

    def test_clone_experiment_creates_new_draft(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = Path(tmp) / "experiments.json"
            created = create_walk_forward_experiment(_payload(), benchmark_report=_benchmark("edge_detected"), store_path=store)
            experiment_id = created["record"]["experiment_id"]
            freeze_walk_forward_experiment(experiment_id, store_path=store)

            cloned = clone_walk_forward_experiment(experiment_id, store_path=store, now="2026-05-06T02:00:00Z")

            self.assertNotEqual(cloned["record"]["experiment_id"], experiment_id)
            self.assertEqual(cloned["record"]["status"], "draft")
            self.assertEqual(cloned["record"]["cloned_from"], experiment_id)
            self.assertEqual(cloned["summary"]["experiment_count"], 2)

    def test_missing_required_fields(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = Path(tmp) / "experiments.json"
            with self.assertRaises(ValidationServiceError) as context:
                create_walk_forward_experiment({"name": "missing windows"}, store_path=store)

            self.assertIn("train_window", context.exception.details["missing_fields"])
            self.assertIn("validation_window", context.exception.details["missing_fields"])
            self.assertIn("test_window", context.exception.details["missing_fields"])

    def test_verdict_mapping_insufficient_data_quality_passed_and_failed(self) -> None:
        self.assertEqual(evaluate_experiment_from_benchmark(_benchmark("insufficient_evidence", rewardable=1))["verdict"], "insufficient_evidence")
        self.assertEqual(evaluate_experiment_from_benchmark(_benchmark("data_quality_too_weak", rewardable=3, quality=30.0))["verdict"], "data_quality_too_weak")
        self.assertEqual(evaluate_experiment_from_benchmark(_benchmark("edge_detected", rewardable=6))["verdict"], "passed")
        self.assertEqual(evaluate_experiment_from_benchmark(_benchmark("weak_edge_detected", rewardable=6))["verdict"], "weak_pass")
        self.assertEqual(evaluate_experiment_from_benchmark(_benchmark("no_edge_detected", rewardable=6, edge=-0.12, lift=-0.2))["verdict"], "failed")

    def test_summary_lists_records_without_exposing_paths(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = Path(tmp) / "experiments.json"
            create_walk_forward_experiment(_payload(data_source="D:\\secret\\raw.log", api_key="not-stored"), benchmark_report=_benchmark("edge_detected"), store_path=store)

            summary = get_walk_forward_summary(store_path=store)
            record = summary["records"][0]

            self.assertEqual(record["data_source"], "[local_path_redacted]")
            self.assertNotIn("not-stored", str(record))
            self.assertEqual(summary["summary"]["experiment_count"], 1)
            self.assertIn("proof_summary", summary)
            self.assertIn("walk_forward_validation_plan", summary)
            self.assertFalse(summary["proof_summary"]["proof_ready"])
            self.assertEqual(summary["summary"]["walk_forward_validation_status"], "blocked_by_evidence")

    def test_walk_forward_proof_ready_with_frozen_out_of_sample_pass_after_costs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = Path(tmp) / "experiments.json"
            created = create_walk_forward_experiment(
                _payload(status="completed"),
                benchmark_report=_benchmark("edge_detected"),
                store_path=store,
            )

            proof = build_walk_forward_proof_summary([created["record"]])

            self.assertTrue(proof["proof_ready"])
            self.assertEqual(proof["status"], "ready_for_human_review")
            self.assertEqual(proof["summary"]["passed_requirement_count"], 6)
            self.assertEqual(proof["summary"]["pass_rate"], 1.0)
            for requirement in proof["requirements"]:
                self.assertFalse(requirement["changes_execution"])
                self.assertFalse(requirement["changes_broker_routes"])
                self.assertFalse(requirement["changes_risk_gates"])
                self.assertFalse(requirement["changes_ranking_weights"])

    def test_walk_forward_validation_plan_blocks_claims_when_no_experiments_exist(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = Path(tmp) / "experiments.json"
            summary = get_walk_forward_summary(store_path=store)

            validation_plan = summary["walk_forward_validation_plan"]
            by_key = {row["key"]: row for row in validation_plan["items"]}
            self.assertEqual(summary["status"], "empty")
            self.assertEqual(validation_plan["status"], "blocked_by_evidence")
            self.assertEqual(summary["summary"]["walk_forward_validation_open_items"], 6)
            self.assertEqual(summary["summary"]["walk_forward_validation_critical_open_items"], 3)
            self.assertEqual(by_key["create_frozen_experiment"]["status"], "no_records")
            self.assertEqual(by_key["chronological_no_lookahead_windows"]["status"], "no_records")
            self.assertEqual(by_key["out_of_sample_result"]["status"], "no_records")
            self.assertIn("repeatability_review", by_key["create_frozen_experiment"]["blocked_claims"])
            self.assertIn("experiment_id", by_key["create_frozen_experiment"]["missing_fields"])
            self.assertFalse(validation_plan["summary"]["claim_permissions"]["public_repeatability_claim"])
            self.assertFalse(validation_plan["summary"]["claim_permissions"]["live_trading_readiness"])
            self.assertTrue(all(item["manual_review_only"] for item in validation_plan["items"]))
            self.assertFalse(any(item["changes_execution"] for item in validation_plan["items"]))

    def test_walk_forward_proof_blocks_lookahead_and_missing_after_costs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = Path(tmp) / "experiments.json"
            bad_windows = _payload(
                status="completed",
                train_window={"start": "2026-05-01", "end": "2026-05-05"},
                validation_window={"start": "2026-05-04", "end": "2026-05-06"},
            )
            benchmark = _benchmark("edge_detected")
            benchmark["sections"]["execution_quality"] = {}
            created = create_walk_forward_experiment(bad_windows, benchmark_report=benchmark, store_path=store)

            proof = build_walk_forward_proof_summary([created["record"]])
            failed_keys = {row["key"] for row in proof["requirements"] if not row["passed"]}

            self.assertFalse(proof["proof_ready"])
            self.assertIn("no_lookahead_windows", failed_keys)
            self.assertIn("after_cost_support", failed_keys)
            self.assertTrue(proof["record_readiness"][0]["warnings"])

    def test_walk_forward_proof_requires_paper_forward_window(self) -> None:
        payload = _payload(status="completed")
        payload.pop("paper_forward_window")

        with tempfile.TemporaryDirectory() as tmp:
            store = Path(tmp) / "experiments.json"
            created = create_walk_forward_experiment(
                payload,
                benchmark_report=_benchmark("edge_detected"),
                store_path=store,
            )

            proof = build_walk_forward_proof_summary([created["record"]])
            no_lookahead = next(row for row in proof["requirements"] if row["key"] == "no_lookahead_windows")

            self.assertFalse(proof["proof_ready"])
            self.assertFalse(no_lookahead["passed"])
            self.assertIn("Train, validation, test, and paper-forward windows are incomplete", proof["record_readiness"][0]["warnings"][0])

    def test_api_response_shape(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = Path(tmp) / "experiments.json"
            client = TestClient(create_app())
            with patch.object(registry, "DEFAULT_STORE_PATH", store), patch.object(
                registry,
                "get_professional_benchmark_summary",
                lambda db=None, current_user=None: _benchmark("edge_detected"),
            ):
                create_response = client.post("/api/walk-forward/experiments", json=_payload())
                self.assertEqual(create_response.status_code, 200)
                created = create_response.json()["data"]
                experiment_id = created["record"]["experiment_id"]

                for path in (
                    "/api/walk-forward/summary",
                    "/api/walk-forward/experiments",
                    f"/api/walk-forward/experiments/{experiment_id}",
                ):
                    response = client.get(path)
                    self.assertEqual(response.status_code, 200)
                    payload = response.json()
                    self.assertTrue(payload["ok"])
                    data = payload["data"]
                    self.assertTrue(data["research_only"])
                    self.assertIn("summary", data)
                    self.assertIn("proof_summary", data)
                    self.assertIn("walk_forward_validation_plan", data)
                    self.assertIn("walk_forward_proof_status", data["summary"])
                    self.assertIn("walk_forward_validation_status", data["summary"])
                    self.assertIn("claim_permissions", data["summary"])
                    self.assertIn("warnings", data)
                    self.assertIn("safety_notes", data)
                    self.assertIn("Does not place orders.", data["safety_notes"])

                freeze_response = client.post(f"/api/walk-forward/experiments/{experiment_id}/freeze")
                self.assertEqual(freeze_response.status_code, 200)
                self.assertEqual(freeze_response.json()["data"]["record"]["status"], "frozen")
                clone_response = client.post(f"/api/walk-forward/experiments/{experiment_id}/clone")
                self.assertEqual(clone_response.status_code, 200)
                self.assertEqual(clone_response.json()["data"]["record"]["status"], "draft")

    def test_service_contains_no_execution_broker_ranking_or_risk_mutation_calls(self) -> None:
        source = inspect.getsource(registry)
        forbidden_calls = (
            "place_order(",
            "submit_order(",
            "clear_kill_switch(",
            "set_broker_route(",
            "update_ranking_weight(",
            "update_risk_config(",
            "enable_live_trading(",
        )
        for call in forbidden_calls:
            self.assertNotIn(call, source)


if __name__ == "__main__":
    unittest.main()
