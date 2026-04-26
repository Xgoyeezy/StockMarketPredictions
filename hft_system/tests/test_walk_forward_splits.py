from __future__ import annotations

import unittest

from hft.optimization.splits import generate_walk_forward_splits
from hft.optimization.types import SessionManifest


class WalkForwardSplitTest(unittest.TestCase):
    def test_walk_forward_splits_are_deterministic_and_leak_free(self) -> None:
        manifests = [
            SessionManifest(
                session_id=f"session-{index:02d}",
                symbol="AAPL",
                trading_day=f"2024-01-0{index + 1}",
                event_count=100,
                start_ts_ns=1000 * index,
                end_ts_ns=(1000 * index) + 999,
                average_spread_bps=2.0,
                volatility_regime="normal",
                liquidity_regime="normal",
            )
            for index in range(6)
        ]

        left = generate_walk_forward_splits(
            manifests,
            train_sessions=3,
            validation_sessions=1,
            holdout_sessions=1,
        )
        right = generate_walk_forward_splits(
            manifests,
            train_sessions=3,
            validation_sessions=1,
            holdout_sessions=1,
        )

        self.assertEqual(left, right)
        self.assertEqual(left[-1].holdout_sessions, ("session-05",))
        for split in left:
            train = set(split.train_sessions)
            validation = set(split.validation_sessions)
            holdout = set(split.holdout_sessions)
            self.assertFalse(train & validation)
            self.assertFalse(train & holdout)
            self.assertFalse(validation & holdout)

