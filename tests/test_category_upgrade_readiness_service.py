from __future__ import annotations

import inspect
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from backend.services import category_upgrade_readiness_service as readiness
from backend.services.category_upgrade_readiness_service import (
    build_category_upgrade_proof_chain,
    build_category_upgrade_readiness_report,
    build_category_upgrade_support_export,
    evaluate_governance_gate,
    evaluate_proof_gates,
    evaluate_risk_visibility_gate,
    write_category_upgrade_readiness_export,
)


def _safe_state(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "paper_first_boundary_preserved": True,
        "alpaca_paper_only_unattended": True,
        "reward_forecast_research_only": True,
        "risk_gates_authoritative": True,
        "broker_routes_unchanged": True,
        "ai_has_no_order_authority": True,
    }
    payload.update(overrides)
    return payload


def _data_ready(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "summary": {
            "completion_rate": 0.91,
            "rewardability_rate": 0.82,
            "benchmark_ready": True,
        }
    }
    payload["summary"].update(overrides)
    return payload


def _benchmark(status: str = "edge_detected", **overrides: object) -> dict[str, object]:
    summary: dict[str, object] = {
        "benchmark_verdict": status,
        "candidate_count": 80,
        "rewardable_count": 50,
        "baseline_relative_edge": 0.18,
        "score_bucket_lift": 0.16,
        "slippage_adjusted_reward": 0.11,
    }
    summary.update(overrides)
    return {
        "status": status,
        "summary": summary,
        "sections": {"execution_quality": {"slippage_adjusted_reward": summary["slippage_adjusted_reward"]}},
    }


def _walk_forward(verdict: str = "passed") -> dict[str, object]:
    return {
        "records": [
            {"experiment_id": "wf-1", "status": "frozen", "metrics": {"verdict": "pending"}},
            {"experiment_id": "wf-2", "status": "completed", "metrics": {"verdict": verdict}},
        ]
    }


def _execution_ready() -> dict[str, object]:
    return {
        "summary": {"slippage_adjusted_reward": 0.09},
        "can_submit_orders": False,
        "can_submit_live_orders": False,
    }


def _portfolio_ready() -> dict[str, object]:
    return {
        "status": "ready",
        "summary": {"portfolio_risk_coverage": 0.9},
        "writes_risk_limits": False,
        "writes_risk_config": False,
    }


def _promotion_ready() -> dict[str, object]:
    return {
        "status": "ready",
        "summary": {"status": "ready"},
        "writes_execution_config": False,
        "can_submit_orders": False,
        "can_submit_live_orders": False,
    }


def _governance_ready() -> dict[str, object]:
    return {
        "rbac_enforced": True,
        "approval_workflows_enforced": True,
        "registries_versioned": True,
        "audit_immutable": True,
    }


def _external_ready() -> dict[str, object]:
    return {
        "security_review_complete": True,
        "legal_review_complete": True,
        "compliance_review_complete": True,
        "firm_grade_report_sanitized": True,
        "environment_separation_verified": True,
        "permission_enforcement_verified": True,
    }


def _category_proof_ready() -> dict[str, object]:
    return {
        "retail_onboarding_complete": True,
        "no_trade_explanation_coverage_complete": True,
        "support_export_sanitized": True,
        "demo_evidence_separated": True,
        "score_bucket_separation_proven": True,
        "multi_regime_stability_proven": True,
        "strategy_approval_traceability_complete": True,
        "release_validation_complete": True,
        "same_opportunity_shadow_mode_complete": True,
        "system_beats_or_improves_human_after_costs": True,
        "data_lineage_complete": True,
        "model_lineage_complete": True,
        "feature_lineage_complete": True,
        "environment_separation_verified": True,
        "permission_enforcement_complete": True,
        "incident_handling_complete": True,
        "firm_grade_reporting_sanitized": True,
    }


class CategoryUpgradeReadinessServiceTests(unittest.TestCase):
    def test_all_non_hft_categories_ready_when_all_required_gates_and_extra_proof_pass(self) -> None:
        report = build_category_upgrade_readiness_report(
            safety_state=_safe_state(),
            data_completeness=_data_ready(),
            benchmark=_benchmark(),
            walk_forward=_walk_forward(),
            execution_quality=_execution_ready(),
            portfolio_risk=_portfolio_ready(),
            research_promotion=_promotion_ready(),
            governance=_governance_ready(),
            external_review=_external_ready(),
            category_proof=_category_proof_ready(),
            generated_at="2026-05-09T00:00:00Z",
        )

        by_key = {row["key"]: row for row in report["categories"]}
        self.assertEqual(by_key["retail_trading_bot"]["status"], "ready_for_rating_review")
        self.assertEqual(by_key["solo_systematic_trader_platform"]["status"], "ready_for_rating_review")
        self.assertEqual(by_key["small_prop_or_small_fund_research_stack"]["status"], "ready_for_rating_review")
        self.assertEqual(by_key["top_discretionary_trader_comparison"]["status"], "ready_for_rating_review")
        self.assertEqual(by_key["institutional_quant_desk_or_enterprise_control_plane"]["status"], "ready_for_rating_review")
        self.assertEqual(by_key["hft_or_elite_execution_platform"]["status"], "future_only")
        self.assertTrue(report["research_only"])
        self.assertTrue(report["read_only"])
        self.assertFalse(report["can_submit_orders"])
        self.assertFalse(report["can_submit_live_orders"])
        self.assertFalse(report["can_grant_ai_order_authority"])
        self.assertIn("category_progress", report)
        self.assertIn("backlog", report)
        self.assertIn("proof decides priority", report["summary"]["proof_first_rule"].lower())
        backlog = {item["key"]: item for item in report["backlog"]}
        self.assertEqual(backlog["market_specialist_desk_registry"]["state"], "future_only")
        self.assertEqual(backlog["off_exchange_liquidity_dashboard"]["state"], "future_only")
        self.assertEqual(backlog["cpp_core_accelerators"]["state"], "future_only")
        progress = {row["category_key"]: row for row in report["category_progress"]}
        self.assertTrue(progress["solo_systematic_trader_platform"]["rating_update_allowed"])
        self.assertEqual(progress["solo_systematic_trader_platform"]["planning_progress_to_10_pct"], 100.0)

    def test_safety_violation_blocks_dependent_categories(self) -> None:
        report = build_category_upgrade_readiness_report(
            safety_state=_safe_state(automatic_ranking_weight_changes=True),
            data_completeness=_data_ready(),
            benchmark=_benchmark(),
            walk_forward=_walk_forward(),
            execution_quality=_execution_ready(),
            category_proof=_category_proof_ready(),
        )

        safety_gate = next(gate for gate in report["gates"] if gate["key"] == "safety_intact")
        self.assertEqual(safety_gate["status"], "blocked")
        self.assertIn("automatic_ranking_weight_changes is true", safety_gate["blockers"])
        non_hft = [row for row in report["categories"] if row["key"] != "hft_or_elite_execution_platform"]
        self.assertTrue(all(row["status"] == "blocked_by_safety" for row in non_hft))
        first_backlog_item = report["summary"]["priority_backlog"][0]
        self.assertEqual(first_backlog_item["key"], "verification_and_safety_audit")
        self.assertEqual(first_backlog_item["state"], "blocked")

    def test_data_and_benchmark_gaps_keep_solo_systematic_in_progress(self) -> None:
        report = build_category_upgrade_readiness_report(
            safety_state=_safe_state(),
            data_completeness=_data_ready(completion_rate=0.4, rewardability_rate=0.2, benchmark_ready=False),
            benchmark=_benchmark("insufficient_evidence", rewardable_count=2, baseline_relative_edge=None, score_bucket_lift=None),
            walk_forward={"records": []},
            execution_quality=_execution_ready(),
            category_proof=_category_proof_ready(),
        )

        solo = next(row for row in report["categories"] if row["key"] == "solo_systematic_trader_platform")
        self.assertEqual(solo["status"], "in_progress")
        self.assertIn("data_complete_enough", solo["missing_or_partial_gates"])
        self.assertIn("benchmark_available", solo["missing_or_partial_gates"])
        self.assertIn("baselines_beaten", solo["missing_or_partial_gates"])
        self.assertIn("walk_forward_passed", solo["missing_or_partial_gates"])
        backlog_keys = [item["key"] for item in report["summary"]["priority_backlog"]]
        self.assertIn("data_completeness_hardening", backlog_keys)
        self.assertIn("professional_benchmark_hardening", backlog_keys)
        progress = next(row for row in report["category_progress"] if row["category_key"] == "solo_systematic_trader_platform")
        self.assertFalse(progress["rating_update_allowed"])
        self.assertLess(progress["planning_progress_to_10_pct"], 100.0)

    def test_proof_chain_maps_gates_to_safe_next_actions_without_mutation(self) -> None:
        report = build_category_upgrade_readiness_report(
            safety_state=_safe_state(),
            data_completeness=_data_ready(completion_rate=0.4, rewardability_rate=0.2, benchmark_ready=False),
            benchmark=_benchmark("insufficient_evidence", rewardable_count=2, baseline_relative_edge=None, score_bucket_lift=None),
            walk_forward={"records": []},
            execution_quality=_execution_ready(),
        )

        chain = build_category_upgrade_proof_chain(report)

        self.assertEqual(chain["summary"]["stage_count"], 9)
        self.assertTrue(chain["research_only"])
        self.assertTrue(chain["read_only"])
        self.assertFalse(chain["can_submit_orders"])
        self.assertFalse(chain["can_change_broker_routes"])
        self.assertFalse(chain["can_bypass_risk_gates"])
        by_gate = {row["gate_key"]: row for row in chain["records"]}
        self.assertEqual(by_gate["safety_intact"]["status"], "passed")
        self.assertEqual(by_gate["data_complete_enough"]["status"], "partial")
        self.assertIn("Fix missing forward returns", by_gate["data_complete_enough"]["safe_next_action"])
        self.assertFalse(by_gate["execution_costs_handled"]["execution_mutation"])
        self.assertFalse(by_gate["execution_costs_handled"]["broker_route_mutation"])
        self.assertFalse(by_gate["risk_visibility_complete"]["risk_gate_mutation"])
        self.assertFalse(by_gate["baselines_beaten"]["ranking_mutation"])
        self.assertFalse(by_gate["safety_intact"]["can_grant_ai_order_authority"])

    def test_hft_requires_separate_future_thesis(self) -> None:
        report = build_category_upgrade_readiness_report(
            hft_thesis={
                "separate_hft_infrastructure_thesis_approved": True,
                "direct_market_access_proven": True,
                "exchange_connectivity_proven": True,
                "colocation_proven": True,
                "order_book_reconstruction_proven": True,
                "queue_position_modeling_proven": True,
                "low_latency_controls_proven": True,
            }
        )

        hft = next(row for row in report["categories"] if row["key"] == "hft_or_elite_execution_platform")
        self.assertEqual(hft["status"], "ready_for_rating_review")
        self.assertEqual(hft["missing_extra_proof"], [])

    def test_risk_visibility_gate_uses_portfolio_proof_when_present(self) -> None:
        gate = evaluate_risk_visibility_gate(
            {
                "status": "ready",
                "summary": {
                    "portfolio_risk_coverage": 0.35,
                    "portfolio_risk_requirements_passed": 4,
                    "portfolio_risk_requirements_total": 9,
                },
                "proof_summary": {
                    "proof_ready": False,
                    "summary": {
                        "portfolio_risk_coverage": 0.35,
                        "passed_requirement_count": 4,
                        "requirement_count": 9,
                    },
                },
                "writes_risk_limits": False,
                "writes_risk_config": False,
            }
        )

        self.assertEqual(gate["status"], "partial")
        self.assertEqual(gate["evidence"]["proof_ready"], False)
        self.assertFalse(gate["evidence"]["writes_risk_limits"])
        self.assertEqual(gate["blockers"], [])

    def test_governance_gate_uses_research_promotion_proof_when_present(self) -> None:
        gate = evaluate_governance_gate(
            {
                "status": "ready",
                "summary": {
                    "promotion_requirements_passed": 9,
                    "promotion_requirements_total": 10,
                },
                "proof_summary": {
                    "proof_ready": False,
                    "summary": {
                        "promotion_traceability_coverage": 0.84,
                        "passed_requirement_count": 9,
                        "requirement_count": 10,
                    },
                },
                "writes_execution_config": False,
                "can_submit_orders": False,
                "can_submit_live_orders": False,
            },
            _governance_ready(),
        )

        self.assertEqual(gate["status"], "partial")
        self.assertFalse(gate["evidence"]["promotion_proof_ready"])
        self.assertEqual(gate["evidence"]["promotion_requirements_passed"], 9.0)
        self.assertIn("promotion_proof_ready", gate["warnings"][0])

    def test_priority_backlog_maps_missing_extra_proof_to_correct_stage(self) -> None:
        partial_proof = _category_proof_ready()
        partial_proof["retail_onboarding_complete"] = False
        partial_proof["support_export_sanitized"] = False
        partial_proof["same_opportunity_shadow_mode_complete"] = False
        partial_proof["system_beats_or_improves_human_after_costs"] = False
        report = build_category_upgrade_readiness_report(
            safety_state=_safe_state(),
            data_completeness=_data_ready(),
            benchmark=_benchmark(),
            walk_forward=_walk_forward(),
            execution_quality=_execution_ready(),
            portfolio_risk=_portfolio_ready(),
            research_promotion=_promotion_ready(),
            governance=_governance_ready(),
            external_review=_external_ready(),
            category_proof=partial_proof,
        )

        backlog = {item["key"]: item for item in report["backlog"]}
        self.assertEqual(backlog["retail_onboarding_demo_mode"]["state"], "next")
        self.assertIn("retail_onboarding_complete", backlog["retail_onboarding_demo_mode"]["missing_extra_proof"])
        self.assertIn("support_export_sanitized", backlog["retail_onboarding_demo_mode"]["missing_extra_proof"])
        self.assertEqual(backlog["human_system_shadow_maturity"]["state"], "next")
        self.assertIn("same_opportunity_shadow_mode_complete", backlog["human_system_shadow_maturity"]["missing_extra_proof"])
        self.assertIn("system_beats_or_improves_human_after_costs", backlog["human_system_shadow_maturity"]["missing_extra_proof"])

    def test_planning_readiness_is_not_a_rating_upgrade(self) -> None:
        partial_proof = _category_proof_ready()
        partial_proof["score_bucket_separation_proven"] = False
        report = build_category_upgrade_readiness_report(
            safety_state=_safe_state(),
            data_completeness=_data_ready(),
            benchmark=_benchmark(),
            walk_forward=_walk_forward(),
            execution_quality=_execution_ready(),
            category_proof=partial_proof,
        )

        solo_progress = next(row for row in report["category_progress"] if row["category_key"] == "solo_systematic_trader_platform")
        self.assertFalse(solo_progress["rating_update_allowed"])
        self.assertIn("Planning estimate only", solo_progress["rating_update_boundary"])
        self.assertNotEqual(solo_progress["planning_readiness_if_reviewed"], "10/10")

    def test_gate_evaluator_exposes_all_nine_gates_in_order(self) -> None:
        gates = evaluate_proof_gates(
            safety_state=_safe_state(),
            data_completeness=_data_ready(),
            benchmark=_benchmark(),
            walk_forward=_walk_forward(),
            execution_quality=_execution_ready(),
            portfolio_risk=_portfolio_ready(),
            research_promotion=_promotion_ready(),
            governance=_governance_ready(),
            external_review=_external_ready(),
        )

        self.assertEqual(list(gates), list(readiness.GATE_ORDER))
        self.assertTrue(all(gate["passed"] for gate in gates.values()))

    def test_current_safety_verification_uses_last_known_snapshot_without_execution_authority(self) -> None:
        last_known = {
            "status": "ready",
            "route": {"active": "broker_paper", "allowed": True, "provider": "alpaca", "mode": "paper"},
            "route_enforcement": {"alpaca_paper_only": True, "active_route": "broker_paper"},
            "trade_proof": {"no_live_order_autonomy": True, "no_signal_direct_to_broker": True},
            "preflight": {"status": "ready"},
            "kill_switch_context": {"active": False},
        }
        with patch("backend.services.trading_safety_service.read_last_known_safety_state", return_value=last_known):
            state = readiness.build_current_safety_verification_state()

        self.assertTrue(state["last_known_safety_snapshot_available"])
        self.assertTrue(state["paper_first_boundary_preserved"])
        self.assertTrue(state["alpaca_paper_only_unattended"])
        self.assertTrue(state["broker_routes_unchanged"])
        self.assertTrue(state["ai_has_no_order_authority"])
        self.assertFalse(state["ai_order_authority"])
        self.assertFalse(state["broker_routes_changed_by_analytics"])
        gate = readiness.evaluate_safety_gate(state)
        self.assertEqual(gate["status"], "passed")

    def test_current_safety_verification_is_partial_when_no_last_known_snapshot_exists(self) -> None:
        with patch("backend.services.trading_safety_service.read_last_known_safety_state", return_value={}):
            state = readiness.build_current_safety_verification_state()

        self.assertFalse(state["last_known_safety_snapshot_available"])
        self.assertFalse(state["paper_first_boundary_preserved"])
        gate = readiness.evaluate_safety_gate(state)
        self.assertEqual(gate["status"], "partial")
        self.assertIn("paper_first_boundary_preserved", gate["evidence"]["missing_required_true"])

    def test_service_contains_no_execution_broker_risk_ai_or_ranking_mutation_calls(self) -> None:
        source = inspect.getsource(readiness)
        forbidden_calls = (
            "place_order(",
            "submit_order(",
            "submit_live_order(",
            "set_broker_route(",
            "update_ranking_weight(",
            "update_risk_config(",
            "set_risk_limit(",
            "set_risk_kill_switch(",
        )
        for call in forbidden_calls:
            self.assertNotIn(call, source)

    def test_support_export_redacts_sensitive_fields_and_local_paths(self) -> None:
        report = build_category_upgrade_readiness_report(
            safety_state=_safe_state(),
            data_completeness=_data_ready(),
            generated_at="2026-05-09T00:00:00Z",
        )
        report["diagnostics"] = {
            "account_id": "ACCT-123456",
            "api_token": "token-value",
            "authorization_header": "Bearer live-auth-token",
            "environment_value": "BROKER_SECRET=unsafe",
            "database_file": "app.db",
            "raw_log": "raw broker response",
            "note": "review artifact at D:\\sensitive\\raw.log",
            "safe_relative_doc": "docs/TEN_OUT_OF_TEN_PROOF_GATES.md",
        }

        export = build_category_upgrade_support_export(report, generated_at="2026-05-09T00:01:00Z")
        serialized = json.dumps(export, sort_keys=True)

        self.assertTrue(export["sanitized"])
        self.assertTrue(export["support_export_safety"]["sanitized"])
        self.assertFalse(export["support_export_safety"]["path_exposed_in_payload"])
        self.assertNotIn("ACCT-123456", serialized)
        self.assertNotIn("token-value", serialized)
        self.assertNotIn("Bearer live-auth-token", serialized)
        self.assertNotIn("BROKER_SECRET=unsafe", serialized)
        self.assertNotIn("app.db", serialized)
        self.assertNotIn("raw broker response", serialized)
        self.assertNotIn("D:\\sensitive\\raw.log", serialized)
        self.assertIn("[redacted]", serialized)
        self.assertIn("[local_path_redacted]", serialized)
        self.assertIn("docs/TEN_OUT_OF_TEN_PROOF_GATES.md", serialized)
        self.assertIn("database_files", export["support_export_safety"]["excludes"])
        self.assertIn("environment_values", export["support_export_safety"]["excludes"])
        self.assertIn("authorization_headers", export["support_export_safety"]["excludes"])

    def test_written_support_export_avoids_absolute_paths_in_result_and_payload(self) -> None:
        report = build_category_upgrade_readiness_report(
            safety_state=_safe_state(),
            data_completeness=_data_ready(),
            generated_at="2026-05-09T00:00:00Z",
        )
        report["debug"] = {"local_path": "C:\\Users\\example\\raw.log", "account_number": "999999"}

        with tempfile.TemporaryDirectory() as tmp:
            output_dir = Path(tmp) / "exports"
            result = write_category_upgrade_readiness_export(
                report,
                output_dir=output_dir,
                generated_at="2026-05-09T00:01:00Z",
            )
            export_path = output_dir / readiness.CATEGORY_UPGRADE_EXPORT_FILENAME
            payload = json.loads(export_path.read_text(encoding="utf-8"))
            result_text = json.dumps(result, sort_keys=True)
            payload_text = json.dumps(payload, sort_keys=True)

        self.assertEqual(result["status"], "written")
        self.assertEqual(result["artifact_reference"], f"exports/{readiness.CATEGORY_UPGRADE_EXPORT_FILENAME}")
        self.assertTrue(result["sanitized"])
        self.assertNotIn(str(output_dir), result_text)
        self.assertNotIn(str(output_dir), payload_text)
        self.assertNotIn("C:\\Users\\example\\raw.log", payload_text)
        self.assertNotIn("999999", payload_text)
        self.assertIn("[redacted]", payload_text)

    def test_report_tracks_documented_scope_and_marks_all_docs_added(self) -> None:
        report = build_category_upgrade_readiness_report(
            safety_state=_safe_state(),
            data_completeness=_data_ready(),
            benchmark=_benchmark(),
            walk_forward=_walk_forward(),
            execution_quality=_execution_ready(),
            category_proof=_category_proof_ready(),
            generated_at="2026-05-09T00:00:00Z",
        )
        coverage = report["documented_scope_coverage"]

        self.assertGreater(coverage["requirement_count"], 0)
        self.assertTrue(coverage["all_documented_scope_added"])
        self.assertTrue(report["summary"]["all_documented_scope_added"])
        self.assertEqual(coverage["not_done_message"], "")
        categories = {row["category_key"] for row in coverage["by_category"]}
        self.assertIn("retail_trading_bot", categories)
        self.assertIn("institutional_quant_desk_or_enterprise_control_plane", categories)
        self.assertEqual(coverage["missing_count"], 0)
        self.assertEqual(coverage["complete_count"], coverage["requirement_count"])

    def test_documented_scope_coverage_accepts_explicit_requirement_evidence(self) -> None:
        checklist = """# Sample

## Retail Trading Bot: 9/10 To 10/10

Governance readiness:

- [ ] Support export excludes secrets, broker records, raw logs, account IDs, raw local paths, and credentials.
- [ ] Custom evidence gate exists.

## HFT Or Elite Execution Platform: 2/10 To 10/10

Product readiness:

- [ ] HFT remains labeled future only unless a separate infrastructure thesis is approved.
"""

        with tempfile.TemporaryDirectory() as tmp:
            checklist_path = Path(tmp) / "TEN_OUT_OF_TEN_ACCEPTANCE_CHECKLIST.md"
            checklist_path.write_text(checklist, encoding="utf-8")
            report = build_category_upgrade_readiness_report(
                category_proof={"support_export_sanitized": True},
                requirement_evidence={"custom_evidence_gate_exists": True},
                acceptance_checklist_path=checklist_path,
                generated_at="2026-05-09T00:00:00Z",
            )

        coverage = report["documented_scope_coverage"]
        statuses = {row["description"]: row["status"] for row in coverage["requirements"]}
        self.assertEqual(coverage["requirement_count"], 3)
        self.assertEqual(statuses["Support export excludes secrets, broker records, raw logs, account IDs, raw local paths, and credentials."], "complete")
        self.assertEqual(statuses["Custom evidence gate exists."], "complete")
        self.assertEqual(statuses["HFT remains labeled future only unless a separate infrastructure thesis is approved."], "complete")
        self.assertTrue(coverage["all_documented_scope_added"])


if __name__ == "__main__":
    unittest.main()
