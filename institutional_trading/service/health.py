from __future__ import annotations
import json
from dataclasses import dataclass, field
from pathlib import Path
from institutional_trading.models import HealthStatus, ServiceHealth, utc_now
@dataclass
class HealthRegistry:
    components: dict[str, ServiceHealth] = field(default_factory=dict)
    def update(self, health: ServiceHealth) -> None: self.components[health.component] = health
    def aggregate(self) -> ServiceHealth:
        if not self.components: return ServiceHealth(HealthStatus.DEGRADED, "service", "No component health checks have run.")
        status = HealthStatus.FAILED if any(x.status == HealthStatus.FAILED for x in self.components.values()) else (HealthStatus.DEGRADED if any(x.status == HealthStatus.DEGRADED for x in self.components.values()) else HealthStatus.HEALTHY)
        return ServiceHealth(status, "service", f"{len(self.components)} components checked.", utc_now(), {"components": {n: h.status.value for n,h in self.components.items()}})
    def write(self, path: str | Path) -> Path:
        out = Path(path); out.parent.mkdir(parents=True, exist_ok=True); tmp = out.with_suffix(out.suffix + ".tmp"); tmp.write_text(json.dumps({"aggregate": self.aggregate().to_record(), "components": {n:h.to_record() for n,h in sorted(self.components.items())}}, indent=2, sort_keys=True), encoding="utf-8"); tmp.replace(out); return out
