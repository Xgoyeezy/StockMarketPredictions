from __future__ import annotations

import random
import unittest

from hft.optimization.calibration import (
    calibrate_queue_model_from_observations,
    evaluate_fill_model,
)


class OptimizationCalibrationTest(unittest.TestCase):
    def test_calibrated_queue_model_reduces_fill_error(self) -> None:
        raw_observations = [
            {
                "visible_depth": 100.0,
                "incoming_trade_size": 95.0,
                "spread_bps": 1.2,
                "imbalance": 0.30,
                "latency_bucket": 0.20,
                "quote_age_ns": 25_000_000.0,
                "target_fill_ratio": 0.95,
            },
            {
                "visible_depth": 120.0,
                "incoming_trade_size": 60.0,
                "spread_bps": 1.8,
                "imbalance": 0.10,
                "latency_bucket": 0.30,
                "quote_age_ns": 60_000_000.0,
                "target_fill_ratio": 0.50,
            },
            {
                "visible_depth": 80.0,
                "incoming_trade_size": 18.0,
                "spread_bps": 4.0,
                "imbalance": 0.05,
                "latency_bucket": 1.20,
                "quote_age_ns": 400_000_000.0,
                "target_fill_ratio": 0.08,
            },
            {
                "visible_depth": 90.0,
                "incoming_trade_size": 45.0,
                "spread_bps": 2.8,
                "imbalance": 0.55,
                "latency_bucket": 0.70,
                "quote_age_ns": 150_000_000.0,
            },
            {
                "visible_depth": 140.0,
                "incoming_trade_size": 75.0,
                "spread_bps": 1.0,
                "imbalance": 0.20,
                "latency_bucket": 0.15,
                "quote_age_ns": 10_000_000.0,
            },
            {
                "visible_depth": 65.0,
                "incoming_trade_size": 55.0,
                "spread_bps": 6.5,
                "imbalance": 0.40,
                "latency_bucket": 1.60,
                "quote_age_ns": 900_000_000.0,
            },
            {
                "visible_depth": 210.0,
                "incoming_trade_size": 140.0,
                "spread_bps": 0.8,
                "imbalance": 0.15,
                "latency_bucket": 0.05,
                "quote_age_ns": 5_000_000.0,
            },
            {
                "visible_depth": 110.0,
                "incoming_trade_size": 30.0,
                "spread_bps": 5.0,
                "imbalance": 0.62,
                "latency_bucket": 1.05,
                "quote_age_ns": 600_000_000.0,
            },
        ]
        observations = []
        rng = random.Random(19)
        for row in raw_observations:
            visible_depth = float(row["visible_depth"])
            incoming_trade_size = float(row["incoming_trade_size"])
            order_size = min(incoming_trade_size, visible_depth)
            base_ratio = max((incoming_trade_size - max(visible_depth - order_size, 0.0)) / max(order_size, 1e-9), 0.0)
            trade_to_depth = incoming_trade_size / max(visible_depth, 1e-9)
            spread_component = float(row["spread_bps"]) / 10.0
            quote_age_seconds = float(row["quote_age_ns"]) / 1_000_000_000.0
            target_fill_ratio = (
                0.08
                + (0.48 * base_ratio)
                + (0.20 * trade_to_depth)
                - (0.18 * spread_component)
                + (0.10 * abs(float(row["imbalance"])))
                - (0.14 * float(row["latency_bucket"]))
                - (0.09 * quote_age_seconds)
                + rng.uniform(-0.01, 0.01)
            )
            observations.append(
                {
                    **row,
                    "target_fill_ratio": max(0.0, min(target_fill_ratio, 1.0)),
                }
            )

        artifact = calibrate_queue_model_from_observations(observations)
        report = evaluate_fill_model(observations, artifact=artifact)

        self.assertEqual(report.observation_count, len(observations))
        self.assertLess(report.calibrated_error, report.baseline_error)
        self.assertGreaterEqual(report.missed_fill_rate, 0.0)
