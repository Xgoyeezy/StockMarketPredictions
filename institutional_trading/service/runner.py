from __future__ import annotations
import json, os
from dataclasses import dataclass, field
from pathlib import Path
from institutional_trading.audit.logger import HashChainedAuditLogger
from institutional_trading.execution.paper import PaperBrokerAdapter
from institutional_trading.models import AuditEvent, HealthStatus, ServiceHealth, utc_now
from institutional_trading.risk.engine import RiskEngine
from institutional_trading.service.health import HealthRegistry
@dataclass
class TradingService:
    broker: PaperBrokerAdapter; risk_engine: RiskEngine; audit_logger: HashChainedAuditLogger; runtime_dir: Path = field(default_factory=lambda: Path("runtime")/"institutional_trading"); health: HealthRegistry = field(default_factory=HealthRegistry)
    def start(self, *, pid: int | None = None, config_path: str | None = None) -> ServiceHealth:
        self.runtime_dir.mkdir(parents=True, exist_ok=True); self.health.update(self.broker.connect()); self.audit_logger.append(AuditEvent("service_start", "system", {"runtime_dir": str(self.runtime_dir), "pid": pid or os.getpid(), "config_path": config_path})); h = self.health.aggregate(); self.health.write(self.runtime_dir/"health.json"); self._write_status(True, h, pid=pid or os.getpid(), config_path=config_path); return h
    def heartbeat(self, *, pid: int | None = None, config_path: str | None = None) -> ServiceHealth:
        kill_request = self.runtime_dir / "kill_switch.json"
        if kill_request.exists() and not self.risk_engine.kill_switch.enabled:
            try:
                payload = json.loads(kill_request.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                payload = {"reason": "malformed_kill_switch_request"}
            self.risk_engine.kill_switch.trip_global(str(payload.get("reason") or "operator_requested"))
        if self.risk_engine.kill_switch.enabled:
            h = ServiceHealth(HealthStatus.FAILED, "risk", f"Global kill switch active: {self.risk_engine.kill_switch.reason}")
            self.health.update(h)
        h = self.health.aggregate(); self.health.write(self.runtime_dir/"health.json"); self._write_status(True, h, pid=pid or os.getpid(), config_path=config_path); return h
    def stop(self, *, pid: int | None = None, config_path: str | None = None) -> ServiceHealth:
        self.runtime_dir.mkdir(parents=True, exist_ok=True); self.audit_logger.append(AuditEvent("service_stop", "system", {"pid": pid or os.getpid(), "config_path": config_path})); h = ServiceHealth(HealthStatus.DEGRADED, "service", "Service stopped."); self.health.update(h); self.health.write(self.runtime_dir/"health.json"); self._write_status(False, h, pid=pid or os.getpid(), config_path=config_path); return h
    def kill(self, reason: str) -> ServiceHealth:
        self.runtime_dir.mkdir(parents=True, exist_ok=True); running = bool(self.status().get("running", False)); (self.runtime_dir/"kill_switch.json").write_text(json.dumps({"reason": reason, "requested_at": utc_now().isoformat()}, indent=2, sort_keys=True), encoding="utf-8"); self.risk_engine.kill_switch.trip_global(reason); self.audit_logger.append(AuditEvent("kill_switch", "risk_manager", {"reason": reason})); h = ServiceHealth(HealthStatus.FAILED, "risk", f"Global kill switch active: {reason}"); self.health.update(h); self.health.write(self.runtime_dir/"health.json"); self._write_status(running, h, pid=os.getpid()); return h
    def status(self) -> dict:
        p = self.runtime_dir/"status.json"
        try:
            return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {"running": False, "health": ServiceHealth(HealthStatus.DEGRADED, "service", "No status file found.").to_record()}
        except json.JSONDecodeError:
            return {"running": False, "health": ServiceHealth(HealthStatus.DEGRADED, "service", "Status file is temporarily unreadable.").to_record()}
    def health_status(self) -> dict:
        p = self.runtime_dir/"health.json"
        try:
            return json.loads(p.read_text(encoding="utf-8")) if p.exists() else self.health.aggregate().to_record()
        except json.JSONDecodeError:
            return ServiceHealth(HealthStatus.DEGRADED, "service", "Health file is temporarily unreadable.").to_record()
    def _write_status(self, running: bool, health: ServiceHealth, *, pid: int | None = None, config_path: str | None = None) -> None:
        payload = {"running": running, "pid": pid, "mode": "paper", "paper_safe": True, "config_path": config_path, "heartbeat_at": utc_now().isoformat(), "health": health.to_record()}
        path = self.runtime_dir/"status.json"; tmp = path.with_suffix(path.suffix + ".tmp"); tmp.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8"); tmp.replace(path)
