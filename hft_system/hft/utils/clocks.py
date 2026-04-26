from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class TimestampWindow:
    start_ns: int
    end_ns: int

    def contains(self, timestamp_ns: int) -> bool:
        return self.start_ns <= int(timestamp_ns) <= self.end_ns


def ns_to_ms(value: int | float) -> float:
    return float(value) / 1_000_000.0


def ms_to_ns(value: int | float) -> int:
    return int(float(value) * 1_000_000.0)
