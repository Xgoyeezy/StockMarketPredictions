from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

from backend.core.config import settings
from backend.services.billing_service import get_billing_entitlements
from backend.services.exceptions import ForbiddenError, ValidationServiceError
from backend.services.execution.base import ExecutionAdapter
from backend.services.execution.provider_registry import get_execution_adapter_for


@dataclass(frozen=True)
class ExecutionRouteDecision:
    allowed: bool
    intent: str
    adapter_key: str
    broker_name: str
    instrument_type: str
    reason: str
    detail: str
    route_correlation_id: str
    entitlement: dict[str, Any] | None = None

    def to_record(self) -> dict[str, Any]:
        return {
            "allowed": self.allowed,
            "intent": self.intent,
            "adapter_key": self.adapter_key,
            "broker_name": self.broker_name,
            "instrument_type": self.instrument_type,
            "reason": self.reason,
            "detail": self.detail,
            "route_correlation_id": self.route_correlation_id,
            "entitlement": self.entitlement,
        }


class ExecutionRouter:
    def __init__(self, *, adapter_provider: Any | None = None, config: Any | None = None) -> None:
        self._adapter_provider = adapter_provider or get_execution_adapter_for
        self._settings = config

    @staticmethod
    def _normalize_text(value: Any, fallback: str) -> str:
        cleaned = str(value or "").strip().lower()
        return cleaned or fallback

    def _resolve_paper_broker_adapter_key(self, *, instrument_type: str) -> str:
        config = self._settings or settings
        instrument_type = self._normalize_text(instrument_type, "listed_option")
        if instrument_type == "listed_option":
            options_provider = self._normalize_text(getattr(config, "options_broker_provider", "alpaca"), "alpaca")
            if options_provider in {"internal", "internal_paper", "internal_simulator"}:
                return "internal_paper"
            return "tradier_paper" if options_provider == "tradier" else "alpaca_paper"
        return "alpaca_paper"

    def _resolve_adapter_key(
        self,
        *,
        requested_intent: str,
        instrument_type: str,
        rollout_readiness: dict[str, Any] | None,
    ) -> tuple[str, str]:
        requested_intent = self._normalize_text(requested_intent, "default")
        instrument_type = self._normalize_text(instrument_type, "listed_option")
        rollout_readiness = dict(rollout_readiness or {})
        allows_live_rollout = bool(rollout_readiness.get("allows_live_rollout"))
        rollout_basis = str(rollout_readiness.get("basis") or "").strip()
        config = self._settings or settings

        if requested_intent in {"desk", "internal_paper"}:
            if requested_intent == "internal_paper":
                return "internal_paper", "internal_paper"
            return "desk", "desk"

        if requested_intent == "broker_paper":
            broker_mode = self._normalize_text(getattr(config, "broker_mode", "internal_paper"), "internal_paper")
            paper_provider = self._normalize_text(getattr(config, "paper_broker_provider", "internal_paper"), "internal_paper")
            if broker_mode in {"legitimate", "legitimate_brokerage", "legitimate_brokerage_paper"} or paper_provider in {
                "legitimate",
                "legitimate_brokerage",
                "legitimate_brokerage_paper",
            }:
                return "legitimate_brokerage_paper", "legitimate_brokerage_paper"
            if broker_mode in {"internal", "internal_paper", "internal_simulator"} or paper_provider in {
                "internal",
                "internal_paper",
                "internal_simulator",
            }:
                return "internal_paper", "internal_paper"
            adapter_key = self._resolve_paper_broker_adapter_key(instrument_type=instrument_type)
            return adapter_key, adapter_key

        if requested_intent == "broker_live":
            if not allows_live_rollout:
                raise ValidationServiceError(
                    "Broker-live routing is still locked. "
                    f"{rollout_basis or 'Paper stability needs more resolved replay and cleaner execution drift before live rollout.'}"
                )
            return "alpaca_live", "alpaca_live"

        adapter_name = self._normalize_text(getattr(config, "execution_adapter", "desk"), "desk")
        if adapter_name == "alpaca_live" and not allows_live_rollout:
            raise ValidationServiceError(
                "Broker-live routing is still locked. "
                f"{rollout_basis or 'Paper stability needs more resolved replay and cleaner execution drift before live rollout.'}"
            )
        return adapter_name, adapter_name

    def _require_broker_execution_entitlement(self, db: Session, current_user: Any) -> dict[str, Any]:
        entitlements = get_billing_entitlements(db, current_user)
        entry = next((item for item in entitlements.get("items", []) if item.get("key") == "broker_execution"), None)
        if not entry or not entry.get("enabled"):
            raise ForbiddenError("Broker execution is not enabled for this tenant plan.")
        return entry

    def resolve_for_open_trade(
        self,
        *,
        request: Any,
        db: Session | None,
        current_user: Any | None,
        rollout_readiness: dict[str, Any] | None = None,
    ) -> tuple[ExecutionAdapter, ExecutionRouteDecision]:
        requested_intent = self._normalize_text(getattr(request, "execution_intent", "default"), "default")
        instrument_type = self._normalize_text(getattr(request, "instrument_type", "listed_option"), "listed_option")
        route_correlation_id = str(uuid.uuid4())

        adapter_key, broker_name = self._resolve_adapter_key(
            requested_intent=requested_intent,
            instrument_type=instrument_type,
            rollout_readiness=rollout_readiness,
        )

        entitlement = None
        if requested_intent in {"broker_paper", "broker_live"} and (db is not None or current_user is not None):
            if db is None or current_user is None:
                raise ValidationServiceError("Broker execution requires an authenticated database-backed session.")
            entitlement = self._require_broker_execution_entitlement(db, current_user)

        decision = ExecutionRouteDecision(
            allowed=True,
            intent=requested_intent,
            adapter_key=adapter_key,
            broker_name=broker_name,
            instrument_type=instrument_type,
            reason="route_resolved",
            detail=f"Execution routed to {adapter_key}.",
            route_correlation_id=route_correlation_id,
            entitlement=entitlement,
        )
        adapter = self._adapter_provider(adapter_key)

        return adapter, decision

    def diagnostics(
        self,
        *,
        db: Session | None,
        current_user: Any | None,
        instrument_type: str = "equity",
        requested_intent: str = "default",
        rollout_readiness: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        instrument_type = self._normalize_text(instrument_type, "equity")
        requested_intent = self._normalize_text(requested_intent, "default")
        resolved: dict[str, Any] = {
            "configured": {
                "execution_adapter": getattr(settings, "execution_adapter", None),
                "broker_mode": getattr(settings, "broker_mode", None),
                "paper_broker_provider": getattr(settings, "paper_broker_provider", None),
                "options_broker_provider": getattr(settings, "options_broker_provider", None),
                "alpaca_live_trading_enabled": bool(getattr(settings, "alpaca_live_trading_enabled", False)),
            },
            "request": {
                "instrument_type": instrument_type,
                "requested_intent": requested_intent,
            },
        }

        try:
            adapter, decision = self.resolve_for_open_trade(
                request=type("RouteProbe", (), {"instrument_type": instrument_type, "execution_intent": requested_intent})(),
                db=db,
                current_user=current_user,
                rollout_readiness=rollout_readiness,
            )
            resolved["decision"] = decision.to_record()
            resolved["adapter"] = {"name": adapter.adapter_name}
        except (ForbiddenError, ValidationServiceError) as exc:
            resolved["decision"] = {
                "allowed": False,
                "intent": requested_intent,
                "adapter_key": None,
                "broker_name": None,
                "instrument_type": instrument_type,
                "reason": getattr(exc, "error_code", "blocked"),
                "detail": str(exc),
                "route_correlation_id": None,
                "entitlement": None,
            }
            resolved["adapter"] = None

        return resolved
