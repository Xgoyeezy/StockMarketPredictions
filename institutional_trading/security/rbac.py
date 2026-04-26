from __future__ import annotations
from dataclasses import dataclass, field
class AccessDenied(PermissionError): pass
DEFAULT_PERMISSIONS = {"viewer": frozenset({"view","health","status"}), "operator": frozenset({"view","health","status","start","stop","reconcile"}), "risk_manager": frozenset({"view","health","status","kill","reconcile"}), "admin": frozenset({"view","health","status","start","stop","kill","submit_order","reconcile"})}
@dataclass(frozen=True)
class RBACPolicy:
    permissions_by_role: dict[str, frozenset[str]] = field(default_factory=lambda: dict(DEFAULT_PERMISSIONS))
    def allowed(self, *, role: str, action: str) -> bool: return action in self.permissions_by_role.get(role, frozenset())
    def assert_allowed(self, *, role: str, action: str) -> None:
        if not self.allowed(role=role, action=action): raise AccessDenied(f"Role {role!r} is not allowed to perform action {action!r}.")
