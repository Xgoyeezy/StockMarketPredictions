from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class DeterministicIdGenerator:
    prefix: str = "id"
    counter: int = 0

    def next(self, kind: str = "evt") -> str:
        self.counter += 1
        return f"{self.prefix}-{kind}-{self.counter:08d}"


@dataclass
class NamedIdPool:
    generators: dict[str, DeterministicIdGenerator] = field(default_factory=dict)

    def next(self, kind: str) -> str:
        if kind not in self.generators:
            self.generators[kind] = DeterministicIdGenerator(prefix=kind)
        return self.generators[kind].next(kind=kind)
