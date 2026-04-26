from __future__ import annotations

import random
import tempfile
from pathlib import Path
import unittest

from hft.latency.model import LatencyModel, LatencyProfile


class LatencyModelTest(unittest.TestCase):
    def test_fixed_profile_is_exact(self) -> None:
        model = LatencyModel(profile=LatencyProfile("fixed", {"fill_ns": 17}), rng=random.Random(7))
        self.assertEqual(model.sample_ns("fill_ns"), 17)

    def test_normal_profile_is_deterministic_for_same_seed(self) -> None:
        left = LatencyModel(
            profile=LatencyProfile("normal", {"fill_ns": {"mean_ns": 100, "std_ns": 10}}),
            rng=random.Random(7),
        )
        right = LatencyModel(
            profile=LatencyProfile("normal", {"fill_ns": {"mean_ns": 100, "std_ns": 10}}),
            rng=random.Random(7),
        )
        self.assertEqual(left.sample_ns("fill_ns"), right.sample_ns("fill_ns"))

    def test_lognormal_profile_is_non_negative(self) -> None:
        model = LatencyModel(
            profile=LatencyProfile("lognormal", {"fill_ns": {"mean_ns": 100, "sigma": 0.3}}),
            rng=random.Random(11),
        )
        self.assertGreaterEqual(model.sample_ns("fill_ns"), 0)

    def test_historical_profile_replays_from_sample_file(self) -> None:
        with tempfile.TemporaryDirectory(prefix="hft_latency_") as tmpdir:
            sample_file = Path(tmpdir) / "samples.txt"
            sample_file.write_text("10\n20\n30\n", encoding="utf-8")
            left = LatencyModel(
                profile=LatencyProfile("historical", {"sample_file": str(sample_file)}),
                rng=random.Random(7),
            )
            right = LatencyModel(
                profile=LatencyProfile("historical", {"sample_file": str(sample_file)}),
                rng=random.Random(7),
            )
            self.assertEqual(left.sample_ns("fill_ns"), right.sample_ns("fill_ns"))


if __name__ == "__main__":
    unittest.main()
