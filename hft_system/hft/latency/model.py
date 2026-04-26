from __future__ import annotations

import random
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class LatencyProfile:
    profile_type: str
    params: dict[str, Any]


@dataclass
class LatencyModel:
    profile: LatencyProfile
    rng: random.Random
    samples: list[int] = field(default_factory=list)

    def sample_ns(self, key: str) -> int:
        config = self.profile.params.get(key, 0)
        if self.profile.profile_type == "fixed":
            return int(config)
        if self.profile.profile_type == "normal":
            mean = float(config.get("mean_ns", 0))
            std = float(config.get("std_ns", 0))
            return max(0, int(self.rng.normalvariate(mean, std)))
        if self.profile.profile_type == "lognormal":
            mean = float(config.get("mean_ns", 1))
            sigma = float(config.get("sigma", 0.1))
            value = self.rng.lognormvariate(0.0, sigma) * mean
            return max(0, int(value))
        if self.profile.profile_type == "historical":
            if not self.samples:
                path = Path(str(self.profile.params.get("sample_file") or ""))
                if path.exists():
                    self.samples = [int(line.strip()) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
            if self.samples:
                return int(self.samples[self.rng.randrange(len(self.samples))])
        return int(config or 0)
