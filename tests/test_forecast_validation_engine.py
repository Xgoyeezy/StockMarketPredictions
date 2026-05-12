from __future__ import annotations

from dataclasses import FrozenInstanceError
import unittest

import pytest

from backend.services.forecast_validation_engine import (
    ActualPoint,
    aggregate_evaluations,
    align_actual_series,
    build_forecast_validation_hardening_plan,
    compute_direction_score,
    compute_path_error,
    compute_path_rmse,
    compute_timing_score,
    evaluate_prediction,
    get_forecast_validation_models,
    get_forecast_validation_predictions,
    get_forecast_validation_regimes,
    get_forecast_validation_summary,
    make_prediction,
)


def _prediction(series: list[tuple[int, float]], *, prediction_id: str = "test", confidence: float = 0.7):
    return make_prediction(
        prediction_id=prediction_id,
        symbol="SPY",
        prediction_created_at="2026-05-01T14:30:00+00:00",
        horizon_minutes=60,
        forecast_series=series,
        predicted_direction="bullish" if series[-1][1] >= series[0][1] else "bearish",
        predicted_target_pct=0.6,
        invalidation_level=98.0 if series[-1][1] >= series[0][1] else 112.0,
        confidence=confidence,
        engine="unit_test_engine",
        source="unit_test_source",
        model_name="unit_test_model",
        regime="trending",
    )


def test_perfect_prediction_scores_cleanly() -> None:
    prediction = _prediction([(0, 100.0), (30, 105.0), (60, 110.0)])
    actual = [(0, 100.0), (30, 105.0), (60, 110.0)]

    result = evaluate_prediction(prediction, actual)

    assert result["missing_data"] == []
    assert result["rewardable"] is True
    assert result["direction_correct"] is True
    assert result["mae"] == pytest.approx(0.0)
    assert result["rmse"] == pytest.approx(0.0)
    assert result["timing_error"] == 0
    assert result["reward"] is not None
    assert result["reward_components"]["path_fit_score"] == pytest.approx(1.0)
    assert result["reward_components"]["confidence_penalty"] == pytest.approx(0.3)


def test_wrong_direction_prediction_sets_direction_score_to_zero() -> None:
    prediction = _prediction([(0, 100.0), (30, 104.0), (60, 108.0)])
    actual = [(0, 100.0), (30, 98.0), (60, 95.0)]

    direction = compute_direction_score(prediction, actual)
    result = evaluate_prediction(prediction, actual)

    assert direction["predicted_direction"] == "up"
    assert direction["actual_direction"] == "down"
    assert direction["score"] == 0.0
    assert result["direction_correct"] is False


def test_high_error_path_has_larger_mae_and_rmse_than_low_error_path() -> None:
    prediction = _prediction([(0, 100.0), (30, 105.0), (60, 110.0)])
    low_error_actual = [(0, 100.0), (30, 105.5), (60, 109.5)]
    high_error_actual = [(0, 96.0), (30, 99.0), (60, 103.0)]

    assert compute_path_error(prediction, high_error_actual) > compute_path_error(prediction, low_error_actual)
    assert compute_path_rmse(prediction, high_error_actual) > compute_path_rmse(prediction, low_error_actual)


def test_timing_mismatch_reports_offset_error() -> None:
    prediction = _prediction([(0, 100.0), (30, 103.0), (60, 108.0)])
    actual = [(0, 100.0), (30, 109.0), (60, 104.0)]

    timing = compute_timing_score(prediction, actual)

    assert timing["predicted_turn_offset"] == 60
    assert timing["actual_turn_offset"] == 30
    assert timing["timing_error"] == 30
    assert 0.0 < timing["score"] < 1.0


def test_missing_actual_data_is_flagged_and_not_rewarded() -> None:
    prediction = _prediction([(0, 100.0), (30, 101.0), (60, 102.0)])
    actual = [ActualPoint(timestamp_offset=0, price=100.0), ActualPoint(timestamp_offset=60, price=102.0)]

    alignment = align_actual_series(prediction, actual)
    result = evaluate_prediction(prediction, actual)

    assert alignment["missing_data"] is True
    assert alignment["missing_offsets"] == [30]
    assert result["missing_data"] == ["actual_series"]
    assert result["reward"] is None
    assert result["rewardable"] is False
    assert "actual_series" in result["missing_data"]


def test_missing_required_forecast_fields_are_visible_not_rewardable() -> None:
    prediction = make_prediction(
        prediction_id="missing-contract",
        symbol="SPY",
        timestamp="2026-05-01T14:30:00+00:00",
        horizon_minutes=60,
        forecast_series=[(0, 100.0), (60, 102.0)],
        confidence=0.8,
        engine="unit_test_engine",
        source="unit_test_source",
        regime="trending",
    )

    result = evaluate_prediction(prediction, [(0, 100.0), (60, 102.0)])

    assert result["rewardable"] is False
    assert "predicted_direction" in result["missing_fields"]
    assert "predicted_target_pct" in result["missing_fields"]
    assert "invalidation_level" in result["missing_fields"]
    assert result["forecast_total_reward"] is None


def test_target_and_invalidation_are_reported() -> None:
    target_hit = evaluate_prediction(
        _prediction([(0, 100.0), (60, 101.0)], prediction_id="target"),
        [(0, 100.0), (30, 100.7), (60, 101.0)],
    )
    invalidated = evaluate_prediction(
        _prediction([(0, 100.0), (60, 101.0)], prediction_id="invalid"),
        [(0, 100.0), (30, 97.5), (60, 101.0)],
    )

    assert target_hit["target_hit"] is True
    assert target_hit["time_to_target"] == 30
    assert invalidated["invalidation_hit"] is True


def test_aggregation_excludes_missing_rewards_from_performance_averages() -> None:
    good = evaluate_prediction(
        _prediction([(0, 100.0), (30, 105.0), (60, 110.0)], prediction_id="good", confidence=0.8),
        [(0, 100.0), (30, 105.0), (60, 110.0)],
    )
    missing = evaluate_prediction(
        _prediction([(0, 100.0), (30, 101.0), (60, 102.0)], prediction_id="missing", confidence=0.4),
        [(0, 100.0), (60, 102.0)],
    )

    aggregate = aggregate_evaluations([good, missing], "engine")[0]

    assert aggregate["engine"] == "unit_test_engine"
    assert aggregate["count"] == 2
    assert aggregate["evaluated_count"] == 1
    assert aggregate["missing_data_count"] == 1
    assert aggregate["avg_reward"] == pytest.approx(good["reward"])


def test_fixture_outputs_have_expected_aggregation_shape() -> None:
    summary = get_forecast_validation_summary()
    predictions = get_forecast_validation_predictions()
    models = get_forecast_validation_models()
    regimes = get_forecast_validation_regimes()

    assert summary["mode"] == "research_only"
    assert summary["research_only"] is True
    assert "safety_notes" in summary
    assert "summary" in summary
    assert "records" in summary
    assert "aggregations" in summary
    assert "reward_formula" in summary
    assert "forecast_validation_hardening_plan" in summary
    assert summary["summary"]["claim_permissions"]["live_trading_readiness"] is False
    assert predictions["count"] >= 3
    assert "forecast_validation_hardening_plan" in predictions
    assert all("forecast_series" in item and "evaluation" in item for item in predictions["items"])
    assert "by_engine" in models and "by_source" in models
    assert "items" in regimes


def test_prediction_object_is_immutable_after_creation() -> None:
    prediction = _prediction([(0, 100.0), (30, 101.0)], prediction_id="immutable")

    with pytest.raises(FrozenInstanceError):
        prediction.symbol = "QQQ"  # type: ignore[misc]

    with pytest.raises(AttributeError):
        prediction.forecast_series.append((60, 102.0))  # type: ignore[attr-defined]


class ForecastValidationEngineUnitTests(unittest.TestCase):
    def test_rewardable_forecast_response_has_unified_safety_shape(self) -> None:
        summary = get_forecast_validation_summary()

        self.assertTrue(summary["research_only"])
        self.assertIn("Research only. Does not affect trading.", summary["safety_notes"])
        self.assertIn("summary", summary)
        self.assertIn("records", summary)
        self.assertIn("aggregations", summary)
        self.assertIn("forecast_validation_hardening_plan", summary)
        self.assertIn("missing_fields", summary)

    def test_forecast_hardening_plan_blocks_claims_when_actual_paths_are_incomplete(self) -> None:
        summary = get_forecast_validation_summary()
        plan = build_forecast_validation_hardening_plan(
            summary=summary["summary"],
            records=summary["records"],
            aggregations=summary["aggregations"],
        )
        by_key = {row["key"]: row for row in plan["items"]}

        self.assertEqual(plan["status"], "blocked_by_evidence")
        self.assertIn("actual_path_coverage", by_key)
        self.assertIn("forecast_accuracy_claim", plan["summary"]["blocked_claims"])
        self.assertIn("automatic_ranking_mutation", plan["summary"]["blocked_claims"])
        self.assertFalse(plan["summary"]["claim_permissions"]["forecast_accuracy_claim"])
        self.assertFalse(plan["summary"]["claim_permissions"]["automatic_ranking_mutation"])
        self.assertFalse(plan["summary"]["claim_permissions"]["live_trading_readiness"])
        self.assertTrue(all(item["manual_review_only"] for item in plan["items"]))
        self.assertFalse(any(item["changes_execution"] for item in plan["items"]))
        self.assertFalse(any(item["changes_order_submission"] for item in plan["items"]))
        self.assertFalse(any(item["changes_broker_routes"] for item in plan["items"]))
        self.assertFalse(any(item["changes_risk_gates"] for item in plan["items"]))
        self.assertFalse(any(item["changes_ranking_weights"] for item in plan["items"]))

    def test_missing_contract_fields_do_not_compute_reward(self) -> None:
        prediction = make_prediction(
            prediction_id="unit-missing-contract",
            symbol="SPY",
            timestamp="2026-05-01T14:30:00+00:00",
            horizon_minutes=60,
            forecast_series=[(0, 100.0), (60, 101.0)],
            confidence=0.8,
            engine="unit_test_engine",
            source="unit_test_source",
        )
        result = evaluate_prediction(prediction, [(0, 100.0), (60, 101.0)])

        self.assertFalse(result["rewardable"])
        self.assertIsNone(result["forecast_total_reward"])
        self.assertIn("predicted_direction", result["missing_fields"])

    def test_forecast_object_remains_immutable(self) -> None:
        prediction = _prediction([(0, 100.0), (60, 101.0)], prediction_id="unit-immutable")

        with self.assertRaises(FrozenInstanceError):
            prediction.symbol = "QQQ"  # type: ignore[misc]


if __name__ == "__main__":
    unittest.main()
