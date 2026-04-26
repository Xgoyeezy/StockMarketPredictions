from __future__ import annotations

import random
from dataclasses import dataclass

from hft.optimization.types import QueueCalibrationArtifact


@dataclass
class QueuePositionModel:
    rng: random.Random
    calibration: QueueCalibrationArtifact = QueueCalibrationArtifact()

    def fill_ratio(
        self,
        *,
        visible_depth: float,
        incoming_trade_size: float,
        order_size: float,
        spread_bps: float = 0.0,
        imbalance: float = 0.0,
        latency_bucket: float = 0.0,
        quote_age_ns: int = 0,
    ) -> float:
        if incoming_trade_size <= 0 or order_size <= 0:
            return 0.0
        queue_ahead = max(visible_depth - order_size, 0.0)
        depletion = max(incoming_trade_size - queue_ahead, 0.0)
        base_ratio = depletion / max(order_size, 1e-9)
        trade_to_depth = incoming_trade_size / max(visible_depth, 1e-9)
        quote_age_seconds = float(quote_age_ns) / 1_000_000_000.0
        score = (
            self.calibration.intercept
            + (self.calibration.base_ratio_weight * base_ratio)
            + (self.calibration.trade_to_depth_weight * trade_to_depth)
            - (self.calibration.spread_bps_weight * max(spread_bps, 0.0) / 10.0)
            + (self.calibration.imbalance_weight * abs(imbalance))
            - (self.calibration.latency_bucket_weight * max(latency_bucket, 0.0))
            - (self.calibration.quote_age_weight * max(quote_age_seconds, 0.0))
        )
        noise = self.rng.uniform(self.calibration.noise_low, self.calibration.noise_high)
        return max(0.0, min(score * noise, 1.0))
