from __future__ import annotations

import json
from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.orm import Session, selectinload

from backend.core.config import settings
from backend.core.database import SessionLocal
from backend.models.saas import AsyncJob, BillingEventRecord, EntitlementRecord, EntitlementUsage, SubscriptionRecord, Tenant
from backend.services.audit_service import record_audit_event
from backend.services.desk_service import normalize_desk_slug
from backend.services.exceptions import ForbiddenError, NotFoundError, ValidationError
from backend.services.workspace_service import list_workspaces

try:  # pragma: no cover - optional dependency
    import stripe
except ImportError:  # pragma: no cover
    stripe = None

PLAN_ORDER = ("starter", "personal", "pro", "professional", "desk", "team", "enterprise", "white-label")
PUBLIC_PLAN_ORDER = ("starter", "pro", "professional", "team", "enterprise")
BILLING_CYCLES = ("monthly", "annual")
_BILLING_EVENT_HISTORY_LIMIT = 8
_BILLING_OPS_HISTORY_LIMIT = 6
_BILLING_PROCESSED_STATUSES = {"processed", "ignored", "duplicate"}
_BILLING_RECOVERY_ACTIONS = {"reconcile", "retry_last_failure", "sync_entitlements"}
_INTERNAL_OWNED_API_ENTITLEMENTS = {
    "advanced_indicators",
    "api_access",
    "broker_execution",
    "realtime_streaming",
}
_INTERNAL_OWNED_API_PROVIDERS = {"local-demo", "local-session"}
_INTERNAL_PAPER_MODES = {
    "internal",
    "internal_paper",
    "internal_simulator",
    "legitimate",
    "legitimate_brokerage",
    "legitimate_brokerage_paper",
}

ENTITLEMENT_META: dict[str, dict[str, str]] = {
    "workspace_count": {"label": "Workspace count", "description": "Saved workspaces available to the tenant."},
    "saved_layouts": {"label": "Saved layouts", "description": "Saved chart and layout states."},
    "organization_members": {"label": "Organization members", "description": "Seats available for tenant members."},
    "realtime_streaming": {"label": "Realtime streaming", "description": "Live market streaming entitlement."},
    "advanced_indicators": {"label": "Advanced indicators", "description": "Advanced chart overlays and signal studies."},
    "tenant_branding": {"label": "Tenant branding", "description": "White-label branding controls."},
    "api_access": {"label": "API access", "description": "Token-based API access."},
    "broker_execution": {"label": "Broker execution", "description": "Order entry and broker-side execution tooling."},
    "priority_support": {"label": "Priority support", "description": "Priority support and operator coverage."},
    "strategy_lifecycle": {"label": "Strategy lifecycle", "description": "Productized strategy command center access."},
    "strategy_versions": {"label": "Strategy versions", "description": "Version history and rollback capacity."},
    "automation_basic": {"label": "Basic automation", "description": "Paper-first automation controls."},
    "automation_advanced": {"label": "Advanced automation", "description": "Advanced automation and approval workflows."},
    "readiness_scoring": {"label": "Readiness scoring", "description": "Strategy readiness scoring and blockers."},
    "promotion_gates": {"label": "Promotion gates", "description": "Promotion workflows across paper and live stages."},
    "risk_engine": {"label": "Risk engine", "description": "Strategy risk policies and pre-trade checks."},
    "audit_replay": {"label": "Audit replay", "description": "Decision replay and evidence trails."},
    "audit_exports": {"label": "Audit exports", "description": "Exportable audit bundles."},
    "execution_quality": {"label": "Execution quality", "description": "Execution score, slippage, and fill analytics."},
    "options_automation": {"label": "Options automation", "description": "Options automation workflow access."},
    "live_canary": {"label": "Live canary", "description": "Live pilot canary and readiness controls."},
    "live_authorizations": {"label": "Live authorizations", "description": "Signed live automation authorization records."},
    "live_sessions": {"label": "Live sessions", "description": "Armed and supervised live trading sessions."},
    "live_order_approvals": {"label": "Live order approvals", "description": "Operator approval queue for live order intents."},
    "multi_account_controls": {"label": "Multi-account controls", "description": "Controls for more than one linked account."},
    "custom_domains": {"label": "Custom domains", "description": "Custom domain delivery and launch routing."},
    "branded_email": {"label": "Branded email", "description": "Branded email sender and delivery controls."},
    "onboarding_templates": {"label": "Onboarding templates", "description": "Tenant onboarding templates and launch presets."},
    "release_channels": {"label": "Release channels", "description": "Tenant-specific release lanes."},
    "partner_webhooks": {"label": "Partner webhooks", "description": "Outbound partner webhook integrations."},
}


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _normalize_plan_key(plan_key: str | None) -> str:
    key = str(plan_key or "").strip().lower()
    if key in {"starter"}:
        return "personal"
    if key in {"professional", "team"}:
        return "desk"
    if key in {"personal", "pro", "desk", "enterprise", "white-label"}:
        return key
    return "personal"


def _coerce_metadata(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _coerce_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if value in (None, "", 0):
        return None
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(float(value), tz=timezone.utc)
    raw = str(value).strip()
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _serialize_limit(value: Any) -> str | None:
    return None if value in (None, "", "none") else str(value)


def _parse_limit(value: Any) -> int | None:
    if value in (None, "", "none"):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _ent(*, enabled: bool, limit: Any) -> dict[str, Any]:
    return {"enabled": enabled, "limit": limit}


def _starter_entitlements() -> dict[str, dict[str, Any]]:
    return {
        "workspace_count": _ent(enabled=True, limit=3),
        "saved_layouts": _ent(enabled=True, limit=10),
        "organization_members": _ent(enabled=True, limit=2),
        "realtime_streaming": _ent(enabled=False, limit=None),
        "advanced_indicators": _ent(enabled=False, limit=None),
        "tenant_branding": _ent(enabled=False, limit=None),
        "api_access": _ent(enabled=False, limit=None),
        "broker_execution": _ent(enabled=True, limit="manual"),
        "priority_support": _ent(enabled=False, limit=None),
        "custom_domains": _ent(enabled=False, limit=0),
        "branded_email": _ent(enabled=False, limit=0),
        "onboarding_templates": _ent(enabled=False, limit=0),
        "release_channels": _ent(enabled=False, limit=0),
        "partner_webhooks": _ent(enabled=False, limit=0),
        "strategy_lifecycle": _ent(enabled=True, limit=1),
        "strategy_versions": _ent(enabled=True, limit=1),
        "automation_basic": _ent(enabled=True, limit="paper"),
        "automation_advanced": _ent(enabled=False, limit=None),
        "readiness_scoring": _ent(enabled=False, limit=None),
        "promotion_gates": _ent(enabled=False, limit=None),
        "risk_engine": _ent(enabled=False, limit=None),
        "audit_replay": _ent(enabled=False, limit=None),
        "audit_exports": _ent(enabled=False, limit=None),
        "execution_quality": _ent(enabled=False, limit=None),
        "options_automation": _ent(enabled=False, limit=None),
        "live_canary": _ent(enabled=False, limit=None),
        "live_authorizations": _ent(enabled=False, limit=0),
        "live_sessions": _ent(enabled=False, limit=0),
        "live_order_approvals": _ent(enabled=False, limit=0),
        "multi_account_controls": _ent(enabled=False, limit=None),
    }


def _merge_entitlements(base: dict[str, dict[str, Any]], **updates: dict[str, Any]) -> dict[str, dict[str, Any]]:
    merged = {key: dict(value) for key, value in base.items()}
    for key, value in updates.items():
        merged[key] = dict(value)
    return merged


PLAN_CATALOG: dict[str, dict[str, Any]] = {
    "personal": {
        "key": "personal",
        "name": "Personal",
        "tagline": "Personal trading research desk with paper-first automation.",
        "monthly_price": 29,
        "annual_price": 249,
        "seats_label": "1 operator",
        "cta_label": "Start personal",
        "public": True,
        "recommended": False,
        "display_order": 10,
        "live_mode": "Paper-first research",
        "target_persona": "Solo trader using a private research desk with paper automation.",
        "billing_pitch": "Research, journaling, and paper-first automation with safety gates on by default.",
        "proof_points": ["Paper evidence trail", "Research workspace", "Safety gates stay on"],
        "support_model": "Standard support",
        "featured_capabilities": ["Research desk", "Paper automation", "Journaling", "Safety ledger"],
        "entitlements": _starter_entitlements(),
    },
    "starter": {
        "key": "starter",
        "name": "Starter",
        "tagline": "Manual live trading and paper automation for one strategy lane.",
        "monthly_price": 99,
        "annual_price": 990,
        "seats_label": "Up to 2 members",
        "cta_label": "Start controlled",
        "public": True,
        "recommended": False,
        "display_order": 10,
        "live_mode": "Manual live trading",
        "target_persona": "Solo operator proving one paper-first strategy lane.",
        "billing_pitch": "Low-cost entry into the control plane without live automation.",
        "proof_points": ["Manual order flow", "Paper automation lane", "Basic evidence trail"],
        "support_model": "Standard support",
        "featured_capabilities": ["Manual live trading", "Paper automation", "One strategy lane", "No live automation"],
        "entitlements": _starter_entitlements(),
    },
    "pro": {
        "key": "pro",
        "name": "Pro",
        "tagline": "Advanced paper automation, readiness, and risk controls for serious iteration.",
        "monthly_price": 79,
        "annual_price": 699,
        "seats_label": "Up to 3 members",
        "cta_label": "Upgrade to Pro",
        "public": True,
        "recommended": False,
        "display_order": 20,
        "live_mode": "Paper-first + readiness",
        "target_persona": "Operator who wants deeper readiness/risk tooling and higher automation limits.",
        "billing_pitch": "Adds the operator-grade safety and readiness surfaces that keep paper automation honest.",
        "proof_points": ["Readiness checks", "Risk controls", "Audit replay basics"],
        "support_model": "Standard support",
        "featured_capabilities": ["Advanced automation", "Readiness scoring", "Risk engine", "Audit replay"],
        "entitlements": _merge_entitlements(
            _starter_entitlements(),
            workspace_count=_ent(enabled=True, limit=10),
            saved_layouts=_ent(enabled=True, limit=50),
            organization_members=_ent(enabled=True, limit=5),
            realtime_streaming=_ent(enabled=True, limit=None),
            advanced_indicators=_ent(enabled=True, limit=None),
            tenant_branding=_ent(enabled=True, limit=None),
            broker_execution=_ent(enabled=True, limit="assisted"),
            strategy_lifecycle=_ent(enabled=True, limit=3),
            strategy_versions=_ent(enabled=True, limit=3),
            automation_basic=_ent(enabled=True, limit=None),
            readiness_scoring=_ent(enabled=True, limit="basic"),
            risk_engine=_ent(enabled=True, limit="basic"),
            execution_quality=_ent(enabled=True, limit="summary"),
            live_authorizations=_ent(enabled=True, limit=3),
            live_sessions=_ent(enabled=False, limit=0),
            live_order_approvals=_ent(enabled=True, limit=100),
        ),
    },
    "professional": {
        "key": "professional",
        "name": "Professional",
        "tagline": "Supervised live automation with readiness, risk, replay, and execution evidence.",
        "monthly_price": 499,
        "annual_price": 4990,
        "seats_label": "Up to 10 members",
        "cta_label": "Recommended",
        "public": True,
        "recommended": True,
        "display_order": 30,
        "live_mode": "Supervised automation",
        "target_persona": "Professional trader or small desk moving from paper evidence to limited live automation.",
        "billing_pitch": "The $499 tier sells proof, gates, and control instead of commodity connectivity.",
        "proof_points": ["Readiness gates", "Risk engine", "Audit replay", "Execution quality"],
        "support_model": "Named onboarding path",
        "featured_capabilities": ["Supervised live automation", "Readiness gates", "Risk engine", "Audit replay", "Execution quality", "Kill switch", "Versioning", "10 strategies"],
        "entitlements": _merge_entitlements(
            _starter_entitlements(),
            workspace_count=_ent(enabled=True, limit=15),
            saved_layouts=_ent(enabled=True, limit=100),
            organization_members=_ent(enabled=True, limit=10),
            realtime_streaming=_ent(enabled=True, limit=None),
            advanced_indicators=_ent(enabled=True, limit=None),
            tenant_branding=_ent(enabled=True, limit=None),
            api_access=_ent(enabled=True, limit=3),
            broker_execution=_ent(enabled=True, limit=None),
            priority_support=_ent(enabled=True, limit="standard"),
            strategy_lifecycle=_ent(enabled=True, limit=10),
            strategy_versions=_ent(enabled=True, limit=10),
            automation_basic=_ent(enabled=True, limit=None),
            automation_advanced=_ent(enabled=True, limit=None),
            readiness_scoring=_ent(enabled=True, limit="full"),
            promotion_gates=_ent(enabled=True, limit=None),
            risk_engine=_ent(enabled=True, limit="full"),
            audit_replay=_ent(enabled=True, limit=None),
            audit_exports=_ent(enabled=True, limit=None),
            execution_quality=_ent(enabled=True, limit="full"),
            options_automation=_ent(enabled=True, limit=None),
            live_canary=_ent(enabled=True, limit=None),
            live_authorizations=_ent(enabled=True, limit=10),
            live_sessions=_ent(enabled=True, limit=10),
            live_order_approvals=_ent(enabled=True, limit=None),
        ),
    },
    "team": {
        "key": "team",
        "name": "Team",
        "tagline": "Multi-user approvals, multi-account live control, roles, and team audit logs.",
        "monthly_price": 899,
        "annual_price": 8990,
        "seats_label": "Up to 20 members",
        "cta_label": "Scale the desk",
        "public": True,
        "recommended": False,
        "display_order": 40,
        "live_mode": "Team-controlled automation",
        "target_persona": "Small professional desk with reviewers, operators, and multiple linked accounts.",
        "billing_pitch": "Adds the operating workflow needed when more than one person can approve risk.",
        "proof_points": ["Multi-user approvals", "Multi-account controls", "Team audit logs"],
        "support_model": "Priority support",
        "featured_capabilities": ["Multi-user approvals", "Multi-account live control", "Role permissions", "Team audit logs", "Priority support", "25 strategies"],
        "entitlements": _merge_entitlements(
            _starter_entitlements(),
            workspace_count=_ent(enabled=True, limit=25),
            saved_layouts=_ent(enabled=True, limit=150),
            organization_members=_ent(enabled=True, limit=20),
            realtime_streaming=_ent(enabled=True, limit=None),
            advanced_indicators=_ent(enabled=True, limit=None),
            tenant_branding=_ent(enabled=True, limit=None),
            api_access=_ent(enabled=True, limit=5),
            broker_execution=_ent(enabled=True, limit=None),
            priority_support=_ent(enabled=True, limit=None),
            onboarding_templates=_ent(enabled=True, limit=5),
            release_channels=_ent(enabled=True, limit=2),
            partner_webhooks=_ent(enabled=True, limit=3),
            strategy_lifecycle=_ent(enabled=True, limit=25),
            strategy_versions=_ent(enabled=True, limit=50),
            automation_basic=_ent(enabled=True, limit=None),
            automation_advanced=_ent(enabled=True, limit=None),
            readiness_scoring=_ent(enabled=True, limit="full"),
            promotion_gates=_ent(enabled=True, limit=None),
            risk_engine=_ent(enabled=True, limit="full"),
            audit_replay=_ent(enabled=True, limit=None),
            audit_exports=_ent(enabled=True, limit=None),
            execution_quality=_ent(enabled=True, limit="full"),
            options_automation=_ent(enabled=True, limit=None),
            live_canary=_ent(enabled=True, limit=None),
            live_authorizations=_ent(enabled=True, limit=25),
            live_sessions=_ent(enabled=True, limit=25),
            live_order_approvals=_ent(enabled=True, limit=None),
            multi_account_controls=_ent(enabled=True, limit=None),
        ),
    },
    "enterprise": {
        "key": "enterprise",
        "name": "Enterprise",
        "tagline": "Custom policies, reporting, retention, and dedicated support for control-plane rollouts.",
        "monthly_price": 2499,
        "annual_price": 24990,
        "seats_label": "Up to 50 members",
        "cta_label": "Talk to sales",
        "public": True,
        "recommended": False,
        "display_order": 50,
        "live_mode": "Custom control plane",
        "target_persona": "Firm or partner that needs custom controls and reporting around live automation.",
        "billing_pitch": "For teams that need custom control-plane policy, retention, and support commitments.",
        "proof_points": ["Custom policies", "Custom reporting", "Custom retention", "Dedicated support"],
        "support_model": "Dedicated support",
        "price_prefix": "from",
        "featured_capabilities": ["Custom policies", "Custom reporting", "Custom retention", "Dedicated support"],
        "entitlements": _merge_entitlements(
            _starter_entitlements(),
            workspace_count=_ent(enabled=True, limit=60),
            saved_layouts=_ent(enabled=True, limit=300),
            organization_members=_ent(enabled=True, limit=50),
            api_access=_ent(enabled=True, limit=15),
            custom_domains=_ent(enabled=True, limit=3),
            branded_email=_ent(enabled=True, limit=3),
            onboarding_templates=_ent(enabled=True, limit=10),
            release_channels=_ent(enabled=True, limit=4),
            partner_webhooks=_ent(enabled=True, limit=10),
            strategy_lifecycle=_ent(enabled=True, limit=None),
            strategy_versions=_ent(enabled=True, limit=None),
            automation_basic=_ent(enabled=True, limit=None),
            automation_advanced=_ent(enabled=True, limit=None),
            readiness_scoring=_ent(enabled=True, limit="full"),
            promotion_gates=_ent(enabled=True, limit=None),
            risk_engine=_ent(enabled=True, limit="full"),
            audit_replay=_ent(enabled=True, limit=None),
            audit_exports=_ent(enabled=True, limit=None),
            execution_quality=_ent(enabled=True, limit="full"),
            options_automation=_ent(enabled=True, limit=None),
            live_canary=_ent(enabled=True, limit=None),
            live_authorizations=_ent(enabled=True, limit=None),
            live_sessions=_ent(enabled=True, limit=None),
            live_order_approvals=_ent(enabled=True, limit=None),
            multi_account_controls=_ent(enabled=True, limit=None),
        ),
    },
    "desk": {
        "key": "desk",
        "name": "Desk",
        "tagline": "Operator workflow with audit, readiness, and execution evidence.",
        "monthly_price": 199,
        "annual_price": 1799,
        "seats_label": "Up to 10 members",
        "cta_label": "Upgrade to Desk",
        "public": True,
        "recommended": True,
        "display_order": 30,
        "live_mode": "Operator desk",
        "target_persona": "Small desk that wants multi-workspace visibility and richer evidence tooling.",
        "billing_pitch": "The desk tier sells control, evidence, and operator workflow—not hype.",
        "proof_points": ["Audit replay", "Execution quality", "Readiness scoring", "Promotion gates"],
        "support_model": "Priority support",
        "featured_capabilities": ["Audit replay", "Execution quality", "Readiness scoring", "Promotion gates", "Priority support"],
        "entitlements": _merge_entitlements(
            _starter_entitlements(),
            organization_members=_ent(enabled=True, limit=10),
            priority_support=_ent(enabled=True, limit=None),
        ),
    },
    "white-label": {
        "key": "white-label",
        "name": "White-label",
        "tagline": "Legacy internal plan for custom control-plane resale and launch operations.",
        "monthly_price": 4999,
        "annual_price": 49988,
        "seats_label": "Up to 100 members",
        "cta_label": "Launch white-label",
        "public": False,
        "recommended": False,
        "display_order": 90,
        "live_mode": "Internal legacy plan",
        "billing_pitch": "Legacy/internal commercial package retained for compatibility.",
        "featured_capabilities": ["Unlimited branding", "White-label delivery", "Highest integration tier"],
        "entitlements": _merge_entitlements(
            _starter_entitlements(),
            workspace_count=_ent(enabled=True, limit=100),
            saved_layouts=_ent(enabled=True, limit=500),
            organization_members=_ent(enabled=True, limit=100),
            realtime_streaming=_ent(enabled=True, limit=None),
            advanced_indicators=_ent(enabled=True, limit=None),
            tenant_branding=_ent(enabled=True, limit=None),
            api_access=_ent(enabled=True, limit=25),
            broker_execution=_ent(enabled=True, limit=None),
            priority_support=_ent(enabled=True, limit=None),
            custom_domains=_ent(enabled=True, limit=10),
            branded_email=_ent(enabled=True, limit=10),
            onboarding_templates=_ent(enabled=True, limit=25),
            release_channels=_ent(enabled=True, limit=8),
            partner_webhooks=_ent(enabled=True, limit=25),
            # UNSPECIFIED: white-label keeps its custom tier behavior; productization entitlements inherit enterprise-grade access for now.
            strategy_lifecycle=_ent(enabled=True, limit=None),
            strategy_versions=_ent(enabled=True, limit=None),
            automation_basic=_ent(enabled=True, limit=None),
            automation_advanced=_ent(enabled=True, limit=None),
            readiness_scoring=_ent(enabled=True, limit="full"),
            promotion_gates=_ent(enabled=True, limit=None),
            risk_engine=_ent(enabled=True, limit="full"),
            audit_replay=_ent(enabled=True, limit=None),
            audit_exports=_ent(enabled=True, limit=None),
            execution_quality=_ent(enabled=True, limit="full"),
            options_automation=_ent(enabled=True, limit=None),
            live_canary=_ent(enabled=True, limit=None),
            live_authorizations=_ent(enabled=True, limit=None),
            live_sessions=_ent(enabled=True, limit=None),
            live_order_approvals=_ent(enabled=True, limit=None),
            multi_account_controls=_ent(enabled=True, limit=None),
        ),
    },
}

PLAN_CATALOG["enterprise"]["entitlements"] = _merge_entitlements(
    PLAN_CATALOG["team"]["entitlements"],
    workspace_count=_ent(enabled=True, limit=60),
    saved_layouts=_ent(enabled=True, limit=300),
    organization_members=_ent(enabled=True, limit=50),
    api_access=_ent(enabled=True, limit=15),
    custom_domains=_ent(enabled=True, limit=3),
    branded_email=_ent(enabled=True, limit=3),
    onboarding_templates=_ent(enabled=True, limit=10),
    release_channels=_ent(enabled=True, limit=4),
    partner_webhooks=_ent(enabled=True, limit=10),
    strategy_lifecycle=_ent(enabled=True, limit=None),
    strategy_versions=_ent(enabled=True, limit=None),
    automation_basic=_ent(enabled=True, limit=None),
    automation_advanced=_ent(enabled=True, limit=None),
    readiness_scoring=_ent(enabled=True, limit="full"),
    promotion_gates=_ent(enabled=True, limit=None),
    risk_engine=_ent(enabled=True, limit="full"),
    audit_replay=_ent(enabled=True, limit=None),
    audit_exports=_ent(enabled=True, limit=None),
    execution_quality=_ent(enabled=True, limit="full"),
    options_automation=_ent(enabled=True, limit=None),
    live_canary=_ent(enabled=True, limit=None),
    multi_account_controls=_ent(enabled=True, limit=None),
)

PLAN_CATALOG["desk"]["entitlements"] = _merge_entitlements(
    PLAN_CATALOG["team"]["entitlements"],
    organization_members=_ent(enabled=True, limit=20),
    priority_support=_ent(enabled=True, limit=None),
)


def get_plan_definition(plan_key: str | None) -> dict[str, Any]:
    raw_key = str(plan_key or "").strip().lower()
    if raw_key in PLAN_CATALOG:
        return PLAN_CATALOG[raw_key]
    return PLAN_CATALOG[_normalize_plan_key(plan_key)]


def build_plan_payload(plan_key: str | None) -> dict[str, Any]:
    plan = get_plan_definition(plan_key)
    payload = {key: plan[key] for key in ("key", "name", "tagline", "monthly_price", "annual_price", "seats_label", "cta_label", "featured_capabilities")}
    for key in (
        "public",
        "recommended",
        "display_order",
        "live_mode",
        "target_persona",
        "billing_pitch",
        "proof_points",
        "support_model",
        "price_prefix",
    ):
        if key in plan:
            payload[key] = plan[key]
    return payload


def list_billing_plans() -> dict[str, Any]:
    items = []
    for key in PLAN_ORDER:
        plan = PLAN_CATALOG[key]
        items.append(
            {
                **build_plan_payload(key),
                "entitlement_count": len(plan["entitlements"]),
                "billing_cycles": list(BILLING_CYCLES),
                "checkout_supported": bool(_stripe_client()),
                "entitlements": [
                    {
                        "key": entitlement_key,
                        "label": ENTITLEMENT_META.get(entitlement_key, {}).get("label", entitlement_key.replace("_", " ").title()),
                        "enabled": bool(config.get("enabled", False)),
                        "limit": _serialize_limit(config.get("limit")),
                    }
                    for entitlement_key, config in plan["entitlements"].items()
                ],
            }
        )
    return {"items": items, "count": len(items)}


def _resolve_tenant(db: Session, current_user: Any) -> Tenant:
    tenant_id = getattr(current_user, "tenant_id", None)
    if not tenant_id:
        raise NotFoundError("No active tenant is set for this session.")
    tenant = db.execute(
        select(Tenant)
        .where(Tenant.id == tenant_id)
        .options(selectinload(Tenant.memberships), selectinload(Tenant.subscriptions), selectinload(Tenant.entitlements))
    ).scalar_one_or_none()
    if tenant is None:
        raise NotFoundError("The active tenant could not be found.")
    return tenant


def _resolve_tenant_by_slug(db: Session, tenant_slug: str | None) -> Tenant:
    slug = normalize_desk_slug(tenant_slug)
    if not slug:
        raise NotFoundError("No tenant slug is available for billing operations.")
    tenant = db.execute(
        select(Tenant)
        .where(Tenant.slug == slug)
        .options(selectinload(Tenant.memberships), selectinload(Tenant.subscriptions), selectinload(Tenant.entitlements))
    ).scalar_one_or_none()
    if tenant is None:
        raise NotFoundError("The requested tenant could not be found for billing operations.")
    return tenant


def ensure_subscription_record(db: Session, tenant: Tenant) -> SubscriptionRecord:
    subscription = next(iter(tenant.subscriptions), None)
    changed = False
    if subscription is None:
        subscription = SubscriptionRecord(tenant=tenant, provider="internal-demo", status="active", plan_key=tenant.plan_key, metadata_json={"managed_mode": "demo"})
        db.add(subscription)
        changed = True

    next_plan_key = subscription.plan_key or tenant.plan_key
    next_provider = subscription.provider or "internal-demo"
    next_status = subscription.status or "active"
    current_metadata = _coerce_metadata(subscription.metadata_json)
    next_metadata = {
        **current_metadata,
        "managed_mode": current_metadata.get("managed_mode") or ("stripe" if next_provider == "stripe" else "demo"),
    }

    if subscription.plan_key != next_plan_key:
        subscription.plan_key = next_plan_key
        changed = True
    if subscription.provider != next_provider:
        subscription.provider = next_provider
        changed = True
    if subscription.status != next_status:
        subscription.status = next_status
        changed = True
    if _coerce_metadata(subscription.metadata_json) != next_metadata:
        subscription.metadata_json = next_metadata
        changed = True

    if changed:
        db.flush()
    return subscription


def sync_tenant_plan_entitlements(db: Session, tenant: Tenant) -> None:
    plan = get_plan_definition(tenant.plan_key)
    existing = {row.key: row for row in tenant.entitlements}
    changed = False
    for key, config in plan["entitlements"].items():
        row = existing.get(key)
        if row is None:
            row = EntitlementRecord(tenant=tenant, key=key)
            db.add(row)
            changed = True

        next_enabled = bool(config.get("enabled", False))
        next_limit_value = _serialize_limit(config.get("limit"))
        next_source = "plan"
        next_metadata = {"plan_key": tenant.plan_key}

        if row.enabled != next_enabled:
            row.enabled = next_enabled
            changed = True
        if row.limit_value != next_limit_value:
            row.limit_value = next_limit_value
            changed = True
        if row.source != next_source:
            row.source = next_source
            changed = True
        if _coerce_metadata(row.metadata_json) != next_metadata:
            row.metadata_json = next_metadata
            changed = True

    if changed:
        db.flush()


def resolve_tenant_entitlements(tenant: Tenant) -> dict[str, Any]:
    plan = get_plan_definition(tenant.plan_key)
    merged: dict[str, dict[str, Any]] = {}
    for key, config in plan["entitlements"].items():
        meta = ENTITLEMENT_META.get(key, {})
        merged[key] = {
            "key": key,
            "label": meta.get("label", key.replace("_", " ").title()),
            "description": meta.get("description", ""),
            "enabled": bool(config.get("enabled", False)),
            "limit": _serialize_limit(config.get("limit")),
            "source": "plan",
            "metadata": {"plan_key": tenant.plan_key},
        }
    for row in tenant.entitlements:
        entry = merged.setdefault(
            row.key,
            {
                "key": row.key,
                "label": ENTITLEMENT_META.get(row.key, {}).get("label", row.key.replace("_", " ").title()),
                "description": ENTITLEMENT_META.get(row.key, {}).get("description", ""),
                "enabled": False,
                "limit": None,
                "source": "plan",
                "metadata": {},
            },
        )
        entry.update({"enabled": bool(row.enabled), "limit": _serialize_limit(row.limit_value), "source": row.source or "plan", "metadata": _coerce_metadata(row.metadata_json)})
    for key, override in _coerce_metadata(tenant.feature_overrides).items():
        if not isinstance(override, dict):
            continue
        entry = merged.setdefault(
            key,
            {
                "key": key,
                "label": ENTITLEMENT_META.get(key, {}).get("label", key.replace("_", " ").title()),
                "description": ENTITLEMENT_META.get(key, {}).get("description", ""),
                "enabled": False,
                "limit": None,
                "source": "override",
                "metadata": {},
            },
        )
        if "enabled" in override:
            entry["enabled"] = bool(override.get("enabled"))
        if "limit" in override:
            entry["limit"] = _serialize_limit(override.get("limit"))
        entry["source"] = "override"
        entry["metadata"] = {"overridden": True}
    items = sorted(merged.values(), key=lambda item: item["key"])
    return {"items": items, "count": len(items)}


def _internal_owned_api_enabled_for_user(current_user: Any) -> bool:
    if not bool(getattr(settings, "internal_owned_api_enabled", False)):
        return False

    provider = str(getattr(current_user, "provider", "") or "").strip().lower()
    mode = str(getattr(current_user, "mode", "") or "").strip().lower()
    if provider not in _INTERNAL_OWNED_API_PROVIDERS and mode not in {"demo", "local"}:
        return False

    broker_mode = str(getattr(settings, "broker_mode", "") or "").strip().lower()
    paper_provider = str(getattr(settings, "paper_broker_provider", "") or "").strip().lower()
    execution_adapter = str(getattr(settings, "execution_adapter", "") or "").strip().lower()
    return any(value in _INTERNAL_PAPER_MODES for value in (broker_mode, paper_provider, execution_adapter))


def _apply_internal_owned_api_entitlements(entitlements: dict[str, Any], current_user: Any) -> dict[str, Any]:
    if not _internal_owned_api_enabled_for_user(current_user):
        return entitlements

    items: list[dict[str, Any]] = []
    seen_keys: set[str] = set()
    for item in entitlements.get("items") or []:
        next_item = dict(item)
        key = str(next_item.get("key") or "").strip()
        seen_keys.add(key)
        if key in _INTERNAL_OWNED_API_ENTITLEMENTS:
            next_item["enabled"] = True
            next_item["source"] = "internal_owned_api"
            next_item["metadata"] = {
                **_coerce_metadata(next_item.get("metadata")),
                "broker_mode": getattr(settings, "broker_mode", "internal_paper"),
                "provider": "internal_owned_api",
                "paper_only": True,
            }
        items.append(next_item)

    for key in sorted(_INTERNAL_OWNED_API_ENTITLEMENTS - seen_keys):
        meta = ENTITLEMENT_META.get(key, {})
        items.append(
            {
                "key": key,
                "label": meta.get("label", key.replace("_", " ").title()),
                "description": meta.get("description", ""),
                "enabled": True,
                "limit": None,
                "source": "internal_owned_api",
                "metadata": {
                    "broker_mode": getattr(settings, "broker_mode", "internal_paper"),
                    "provider": "internal_owned_api",
                    "paper_only": True,
                },
            }
        )

    items.sort(key=lambda item: item["key"])
    return {"items": items, "count": len(items)}


def get_billing_entitlements(db: Session, current_user: Any) -> dict[str, Any]:
    tenant = _resolve_tenant(db, current_user)
    return _apply_internal_owned_api_entitlements(resolve_tenant_entitlements(tenant), current_user)


def has_entitlement(db: Session, current_user: Any, entitlement_key: str) -> bool:
    entry = next((item for item in get_billing_entitlements(db, current_user)["items"] if item["key"] == entitlement_key), None)
    return bool(entry and entry["enabled"])


def require_entitlement(db: Session, current_user: Any, entitlement_key: str, *, message: str | None = None) -> dict[str, Any]:
    entry = next((item for item in get_billing_entitlements(db, current_user)["items"] if item["key"] == entitlement_key), None)
    if not entry or not entry["enabled"]:
        raise ForbiddenError(message or f"This tenant plan does not allow {entitlement_key.replace('_', ' ')}.")
    return entry


def enforce_entitlement_limit(db: Session, current_user: Any, entitlement_key: str, *, requested_total: int, resource_label: str) -> None:
    entry = require_entitlement(db, current_user, entitlement_key)
    limit = _parse_limit(entry.get("limit"))
    if limit is not None and int(requested_total) > limit:
        raise ValidationError(f"This tenant has reached the plan limit for {resource_label}.")


def increment_entitlement_usage(
    db: Session,
    current_user: Any,
    entitlement_key: str,
    *,
    amount: int = 1,
    period_key: str | None = None,
) -> dict[str, Any]:
    tenant = _resolve_tenant(db, current_user)
    entry = require_entitlement(db, current_user, entitlement_key)
    normalized_period = str(period_key or datetime.now(timezone.utc).strftime("%Y-%m")).strip()
    row = db.execute(
        select(EntitlementUsage).where(
            EntitlementUsage.tenant_id == tenant.id,
            EntitlementUsage.period_key == normalized_period,
            EntitlementUsage.metric_key == entitlement_key,
        )
    ).scalar_one_or_none()
    limit = _parse_limit(entry.get("limit"))
    if row is None:
        row = EntitlementUsage(
            tenant_id=tenant.id,
            period_key=normalized_period,
            metric_key=entitlement_key,
            used_count=0,
            limit_count=limit,
        )
        db.add(row)
    row.used_count = int(row.used_count or 0) + max(0, int(amount or 0))
    row.limit_count = limit
    row.updated_at = _utc_now()
    db.flush()
    return {
        "tenant_id": tenant.id,
        "period_key": row.period_key,
        "metric_key": row.metric_key,
        "used_count": row.used_count,
        "limit_count": row.limit_count,
    }


def _stripe_client() -> Any | None:
    secret_key = str(getattr(settings, "stripe_secret_key", "") or "").strip()
    if stripe is None or not secret_key:
        return None
    stripe.api_key = secret_key
    return stripe


def _price_id_for(plan_key: str, billing_cycle: str) -> str:
    normalized_plan = _normalize_plan_key(plan_key)
    normalized_cycle = str(billing_cycle or "monthly").strip().lower()
    return {
        ("starter", "monthly"): getattr(settings, "stripe_price_starter_monthly", ""),
        ("starter", "annual"): getattr(settings, "stripe_price_starter_annual", ""),
        ("pro", "monthly"): getattr(settings, "stripe_price_pro_monthly", ""),
        ("pro", "annual"): getattr(settings, "stripe_price_pro_annual", ""),
        ("team", "monthly"): getattr(settings, "stripe_price_team_monthly", ""),
        ("team", "annual"): getattr(settings, "stripe_price_team_annual", ""),
        ("enterprise", "monthly"): getattr(settings, "stripe_price_enterprise_monthly", ""),
        ("enterprise", "annual"): getattr(settings, "stripe_price_enterprise_annual", ""),
        ("white-label", "monthly"): getattr(settings, "stripe_price_white_label_monthly", ""),
        ("white-label", "annual"): getattr(settings, "stripe_price_white_label_annual", ""),
    }.get((normalized_plan, normalized_cycle), "")


def _update_subscription_metadata(subscription: SubscriptionRecord, **updates: Any) -> dict[str, Any]:
    metadata = _coerce_metadata(subscription.metadata_json)
    for key, value in updates.items():
        if value is None:
            continue
        metadata[key] = value
    subscription.metadata_json = metadata
    return metadata


def _serialize_subscription(subscription: SubscriptionRecord) -> dict[str, Any]:
    metadata = _coerce_metadata(subscription.metadata_json)
    provider = str(subscription.provider or "internal-demo").strip() or "internal-demo"
    managed_mode = str(metadata.get("managed_mode") or ("stripe" if provider == "stripe" else "demo")).strip().lower()
    raw_plan_key = str(subscription.plan_key or "").strip().lower()
    serialized_plan_key = raw_plan_key if raw_plan_key in PLAN_CATALOG else _normalize_plan_key(subscription.plan_key)
    return {
        "id": subscription.id,
        "provider": provider,
        "status": str(subscription.status or "inactive").strip().lower() or "inactive",
        "plan_key": serialized_plan_key,
        "external_customer_id": subscription.external_customer_id,
        "external_subscription_id": subscription.external_subscription_id,
        "current_period_end": subscription.current_period_end.isoformat() if subscription.current_period_end else None,
        "managed_mode": managed_mode,
        "billing_cycle": str(metadata.get("billing_cycle") or "monthly").strip().lower() or "monthly",
        "last_reconciled_at": metadata.get("last_reconciled_at"),
        "last_synced_at": metadata.get("last_synced_at"),
        "last_webhook_event_id": metadata.get("last_webhook_event_id"),
    }


def _build_usage_entry(limit_item: dict[str, Any] | None, *, used: int) -> dict[str, Any]:
    raw_limit = None if not limit_item else limit_item.get("limit")
    limit_value = _parse_limit(raw_limit)
    remaining = None if limit_value is None else max(limit_value - int(used), 0)
    return {"used": int(used), "limit": raw_limit, "remaining": remaining}


def _build_usage_snapshot(tenant: Tenant, current_user: Any) -> dict[str, Any]:
    entitlement_items = {item["key"]: item for item in resolve_tenant_entitlements(tenant)["items"]}
    user_id = str(getattr(current_user, "user_id", "") or getattr(current_user, "auth_subject", "") or settings.demo_user_id).strip() or settings.demo_user_id
    tenant_slug = str(getattr(current_user, "tenant_slug", "") or tenant.slug).strip().lower() or tenant.slug
    workspace_listing = list_workspaces(user_id, tenant_slug=tenant_slug)
    workspace_count = int(workspace_listing.get("count", 0) or 0)
    active_members = sum(1 for membership in tenant.memberships if str(membership.status or "").strip().lower() == "active")
    return {
        "workspaces": _build_usage_entry(entitlement_items.get("workspace_count"), used=workspace_count),
        "layouts": _build_usage_entry(entitlement_items.get("saved_layouts"), used=workspace_count),
        "members": _build_usage_entry(entitlement_items.get("organization_members"), used=active_members),
    }


def _serialize_billing_event(row: BillingEventRecord) -> dict[str, Any]:
    return {
        "id": row.id,
        "provider": row.provider,
        "source": row.source,
        "event_key": row.event_key,
        "external_event_id": row.external_event_id,
        "status": row.status,
        "plan_key": row.plan_key,
        "billing_cycle": row.billing_cycle,
        "external_customer_id": row.external_customer_id,
        "external_subscription_id": row.external_subscription_id,
        "received_at": row.received_at.isoformat() if row.received_at else None,
        "processed_at": row.processed_at.isoformat() if row.processed_at else None,
        "error_message": row.error_message,
        "payload": _coerce_metadata(row.payload_json),
        "result": _coerce_metadata(row.result_json),
    }


def _record_billing_event(
    db: Session,
    *,
    tenant: Tenant | None,
    provider: str,
    source: str,
    event_key: str,
    status: str = "recorded",
    external_event_id: str | None = None,
    plan_key: str | None = None,
    billing_cycle: str | None = None,
    external_customer_id: str | None = None,
    external_subscription_id: str | None = None,
    payload: dict[str, Any] | None = None,
    result: dict[str, Any] | None = None,
    error_message: str | None = None,
) -> BillingEventRecord:
    normalized_status = str(status or "recorded").strip().lower() or "recorded"
    row = BillingEventRecord(
        tenant=tenant,
        provider=str(provider or "internal-demo").strip().lower() or "internal-demo",
        source=str(source or "system").strip().lower() or "system",
        event_key=str(event_key or "billing.event").strip(),
        external_event_id=str(external_event_id or "").strip() or None,
        status=normalized_status,
        plan_key=_normalize_plan_key(plan_key) if plan_key else None,
        billing_cycle=str(billing_cycle or "").strip().lower() or None,
        external_customer_id=str(external_customer_id or "").strip() or None,
        external_subscription_id=str(external_subscription_id or "").strip() or None,
        payload_json=_coerce_metadata(payload),
        result_json=_coerce_metadata(result),
        error_message=str(error_message or "").strip() or None,
        processed_at=_utc_now() if normalized_status in (_BILLING_PROCESSED_STATUSES | {"failed", "replayed"}) else None,
    )
    db.add(row)
    db.flush()
    return row


def _build_billing_event_snapshot(db: Session, tenant: Tenant) -> dict[str, Any]:
    rows = list(
        db.execute(
            select(BillingEventRecord)
            .where(BillingEventRecord.tenant_id == tenant.id)
            .order_by(BillingEventRecord.received_at.desc(), BillingEventRecord.created_at.desc())
        ).scalars()
    )
    status_counts = Counter(str(row.status or "recorded").strip().lower() or "recorded" for row in rows)
    latest_processed = next((row for row in rows if row.status in _BILLING_PROCESSED_STATUSES or row.status == "replayed"), None)
    latest_failed = next((row for row in rows if str(row.status or "").strip().lower() == "failed"), None)
    return {
        "count": len(rows),
        "status_counts": dict(status_counts),
        "items": [_serialize_billing_event(row) for row in rows[:_BILLING_EVENT_HISTORY_LIMIT]],
        "last_event_key": rows[0].event_key if rows else None,
        "last_event_at": rows[0].received_at.isoformat() if rows and rows[0].received_at else None,
        "last_processed_at": latest_processed.processed_at.isoformat() if latest_processed and latest_processed.processed_at else None,
        "last_failed_at": latest_failed.received_at.isoformat() if latest_failed and latest_failed.received_at else None,
        "recent_failure_count": status_counts.get("failed", 0),
        "duplicate_count": status_counts.get("duplicate", 0),
        "latest_failed_event_id": latest_failed.id if latest_failed else None,
        "latest_failed_external_event_id": latest_failed.external_event_id if latest_failed else None,
    }


def _read_billing_recovery_state(subscription: SubscriptionRecord) -> dict[str, Any]:
    metadata = _coerce_metadata(subscription.metadata_json)
    state = metadata.get("billing_recovery") or {}
    return dict(state) if isinstance(state, dict) else {}


def _write_billing_recovery_state(subscription: SubscriptionRecord, **updates: Any) -> dict[str, Any]:
    metadata = _coerce_metadata(subscription.metadata_json)
    state = dict(metadata.get("billing_recovery") or {})
    history = list(state.get("history") or [])
    new_history_item = updates.pop("history_item", None)
    for key, value in updates.items():
        state[key] = value
    if isinstance(new_history_item, dict):
        history.insert(0, new_history_item)
    state["history"] = history[:_BILLING_OPS_HISTORY_LIMIT]
    metadata["billing_recovery"] = state
    subscription.metadata_json = metadata
    return state


def _build_billing_recovery_snapshot(
    db: Session,
    tenant: Tenant,
    subscription: SubscriptionRecord,
    event_snapshot: dict[str, Any],
) -> dict[str, Any]:
    from backend.services.job_queue_service import get_job_metrics_snapshot

    recovery_state = _read_billing_recovery_state(subscription)
    jobs = get_job_metrics_snapshot(db, tenant_id=tenant.id, job_type="billing_reconciliation")
    failed_event_count = int(event_snapshot.get("recent_failure_count", 0) or 0)
    latest_failed_event_id = event_snapshot.get("latest_failed_event_id")
    available_actions = ["reconcile", "sync_entitlements"]
    if failed_event_count > 0 or latest_failed_event_id:
        available_actions.insert(1, "retry_last_failure")
    return {
        "enabled": True,
        "last_reconciled_at": recovery_state.get("last_reconciled_at"),
        "last_recovery_action": recovery_state.get("last_recovery_action"),
        "last_recovery_status": recovery_state.get("last_recovery_status"),
        "last_recovery_error": recovery_state.get("last_recovery_error"),
        "latest_failed_event_id": latest_failed_event_id,
        "latest_failed_event_at": event_snapshot.get("last_failed_at"),
        "failed_event_count": failed_event_count,
        "pending_job_count": int(jobs.get("summary", {}).get("pending", 0) or 0),
        "available_actions": available_actions,
        "recent_jobs": jobs.get("recent_jobs") or [],
        "jobs": jobs,
        "history": list(recovery_state.get("history") or []),
    }


def _build_billing_sync_snapshot(
    subscription: SubscriptionRecord,
    event_snapshot: dict[str, Any],
    recovery_snapshot: dict[str, Any],
) -> dict[str, Any]:
    serialized = _serialize_subscription(subscription)
    managed_mode = serialized["managed_mode"]
    last_processed_at = _coerce_datetime(event_snapshot.get("last_processed_at"))
    last_failed_at = _coerce_datetime(event_snapshot.get("last_failed_at"))
    stale_cutoff = _utc_now() - timedelta(hours=max(1, int(getattr(settings, "billing_sync_stale_hours", 48) or 48)))
    provider_label = "Stripe billing" if managed_mode == "stripe" else "Demo billing"

    if managed_mode == "demo":
        status = "demo"
        message = "Demo billing is active; Stripe sync is not required."
    elif last_processed_at and last_processed_at <= stale_cutoff:
        status = "stale"
        message = "Stripe billing sync is stale and should be reconciled."
    elif recovery_snapshot.get("failed_event_count"):
        status = "attention"
        message = "Stripe billing has failed events that should be replayed or reconciled."
    elif last_failed_at and not last_processed_at:
        status = "attention"
        message = "Stripe billing has failed events and no successful sync yet."
    else:
        status = "healthy"
        message = "Stripe billing is healthy."

    available_actions = list(recovery_snapshot.get("available_actions") or [])
    needs_reconciliation = status in {"attention", "stale"} or bool(recovery_snapshot.get("pending_job_count"))
    return {
        "status": status,
        "message": message if managed_mode == "demo" else f"{provider_label}: {message.removeprefix('Stripe billing ').capitalize()}" if message.startswith("Stripe billing ") else message,
        "provider": serialized["provider"],
        "managed_mode": managed_mode,
        "last_event_key": event_snapshot.get("last_event_key"),
        "last_event_at": event_snapshot.get("last_event_at"),
        "last_processed_at": event_snapshot.get("last_processed_at"),
        "last_failed_at": event_snapshot.get("last_failed_at"),
        "recent_failure_count": int(event_snapshot.get("recent_failure_count", 0) or 0),
        "duplicate_count": int(event_snapshot.get("duplicate_count", 0) or 0),
        "needs_reconciliation": needs_reconciliation,
        "available_actions": available_actions if needs_reconciliation or managed_mode == "stripe" else [],
    }


def _build_billing_ops_snapshot(
    db: Session,
    tenant: Tenant,
    subscription: SubscriptionRecord,
    event_snapshot: dict[str, Any],
    recovery_snapshot: dict[str, Any],
    sync_snapshot: dict[str, Any],
) -> dict[str, Any]:
    drills = list(recovery_snapshot.get("history") or [])
    replay_items = [item for item in drills if str(item.get("kind") or "").strip().lower() == "replay"]
    summary_status = sync_snapshot.get("status") or "unknown"
    if recovery_snapshot.get("pending_job_count") or recovery_snapshot.get("failed_event_count"):
        summary_status = "warning" if summary_status == "healthy" else summary_status
    return {
        "tenant": {
            "id": tenant.id,
            "slug": tenant.slug,
            "name": tenant.name,
            "status": tenant.status,
            "plan_key": tenant.plan_key,
            "provider": subscription.provider,
        },
        "summary": {
            "status": summary_status,
            "message": sync_snapshot.get("message"),
            "needs_attention": bool(sync_snapshot.get("needs_reconciliation")) or bool(recovery_snapshot.get("failed_event_count")),
            "pending_job_count": int(recovery_snapshot.get("pending_job_count", 0) or 0),
            "failed_event_count": int(recovery_snapshot.get("failed_event_count", 0) or 0),
            "drill_count": len(drills),
            "replay_count": len(replay_items),
            "last_drill_at": drills[0].get("created_at") if drills else None,
            "last_replay_at": replay_items[0].get("created_at") if replay_items else None,
        },
        "sync": sync_snapshot,
        "recovery": recovery_snapshot,
        "events": event_snapshot,
        "drills": {"count": len(drills), "items": drills[:_BILLING_OPS_HISTORY_LIMIT]},
        "jobs": recovery_snapshot.get("jobs") or {},
    }


def get_billing_summary(db: Session, current_user: Any) -> dict[str, Any]:
    tenant = _resolve_tenant(db, current_user)
    subscription = ensure_subscription_record(db, tenant)
    sync_tenant_plan_entitlements(db, tenant)
    event_snapshot = _build_billing_event_snapshot(db, tenant)
    recovery_snapshot = _build_billing_recovery_snapshot(db, tenant, subscription, event_snapshot)
    sync_snapshot = _build_billing_sync_snapshot(subscription, event_snapshot, recovery_snapshot)
    return {
        "tenant": {
            "id": tenant.id,
            "slug": tenant.slug,
            "name": tenant.name,
            "status": tenant.status,
            "plan_key": tenant.plan_key,
            "billing_email": tenant.billing_email,
        },
        "plan": build_plan_payload(tenant.plan_key),
        "subscription": _serialize_subscription(subscription),
        "usage": _build_usage_snapshot(tenant, current_user),
        "entitlements": _apply_internal_owned_api_entitlements(resolve_tenant_entitlements(tenant), current_user),
        "sync": sync_snapshot,
        "recovery": recovery_snapshot,
        "events": event_snapshot,
        "checkout": {
            "configured": bool(_stripe_client()),
            "mode": "live" if _stripe_client() else "demo",
            "publishable_key": str(getattr(settings, "stripe_publishable_key", "") or "").strip() or None,
        },
    }


def _emit_plan_change_side_effects(db: Session, tenant: Tenant, *, previous_plan_key: str | None, current_plan_key: str, source: str) -> None:
    record_audit_event(
        db,
        event_type="billing.plan_changed",
        tenant=tenant,
        payload={"previous_plan_key": previous_plan_key, "plan_key": current_plan_key, "source": source},
    )
    try:
        from backend.services.tenant_service import _dispatch_partner_webhook_event

        _dispatch_partner_webhook_event(
            db,
            tenant=tenant,
            event_key="billing.plan_changed",
            payload={
                "tenant_slug": tenant.slug,
                "tenant_name": tenant.name,
                "previous_plan_key": previous_plan_key,
                "plan_key": current_plan_key,
                "source": source,
                "changed_at": _utc_now().isoformat(),
            },
        )
    except Exception:
        pass


def change_tenant_plan(db: Session, current_user: Any, plan_key: str) -> dict[str, Any]:
    normalized_plan_key = _normalize_plan_key(plan_key)
    tenant = _resolve_tenant(db, current_user)
    previous_plan_key = tenant.plan_key
    tenant.plan_key = normalized_plan_key
    if not tenant.billing_email:
        tenant.billing_email = str(getattr(current_user, "email", "") or "").strip() or tenant.billing_email
    subscription = ensure_subscription_record(db, tenant)
    subscription.plan_key = normalized_plan_key
    if subscription.provider != "stripe":
        subscription.provider = "internal-demo"
    subscription.status = "active"
    _update_subscription_metadata(
        subscription,
        managed_mode="stripe" if subscription.provider == "stripe" else "demo",
        last_synced_at=_utc_now().isoformat(),
    )
    sync_tenant_plan_entitlements(db, tenant)
    _record_billing_event(
        db,
        tenant=tenant,
        provider=subscription.provider,
        source="plan_change",
        event_key="billing.plan_changed",
        status="processed",
        plan_key=normalized_plan_key,
        payload={"previous_plan_key": previous_plan_key, "plan_key": normalized_plan_key},
        result={"changed": previous_plan_key != normalized_plan_key},
    )
    _emit_plan_change_side_effects(db, tenant, previous_plan_key=previous_plan_key, current_plan_key=normalized_plan_key, source="manual")
    db.commit()
    return get_billing_summary(db, current_user)


def create_billing_checkout_session(
    db: Session,
    current_user: Any,
    *,
    plan_key: str,
    billing_cycle: str = "monthly",
    success_url: str | None = None,
    cancel_url: str | None = None,
) -> dict[str, Any]:
    requested_plan_key = str(plan_key or "").strip().lower()
    normalized_plan_key = requested_plan_key if requested_plan_key in PLAN_CATALOG else _normalize_plan_key(plan_key)
    normalized_cycle = str(billing_cycle or "monthly").strip().lower()
    tenant = _resolve_tenant(db, current_user)
    subscription = ensure_subscription_record(db, tenant)
    client = _stripe_client()
    price_id = _price_id_for(normalized_plan_key, normalized_cycle)
    if client is None or not price_id:
        previous_plan_key = tenant.plan_key
        tenant.plan_key = normalized_plan_key
        subscription.plan_key = normalized_plan_key
        subscription.provider = "internal-demo"
        subscription.status = "active"
        _update_subscription_metadata(subscription, managed_mode="demo", billing_cycle=normalized_cycle, last_synced_at=_utc_now().isoformat())
        sync_tenant_plan_entitlements(db, tenant)
        _record_billing_event(
            db,
            tenant=tenant,
            provider="internal-demo",
            source="checkout",
            event_key="billing.checkout_demo",
            status="processed",
            plan_key=normalized_plan_key,
            billing_cycle=normalized_cycle,
            payload={"previous_plan_key": previous_plan_key, "plan_key": normalized_plan_key},
            result={"mode": "demo"},
        )
        _emit_plan_change_side_effects(db, tenant, previous_plan_key=previous_plan_key, current_plan_key=normalized_plan_key, source="checkout_demo")
        db.commit()
        return {
            "mode": "demo",
            "configured": False,
            "billing_cycle": normalized_cycle,
            "url": None,
            "message": f"Demo checkout applied the {normalized_plan_key} plan.",
            "summary": get_billing_summary(db, current_user),
        }

    checkout_session = client.checkout.Session.create(
        mode="subscription",
        line_items=[{"price": price_id, "quantity": 1}],
        success_url=success_url or settings.billing_checkout_success_url,
        cancel_url=cancel_url or settings.billing_checkout_cancel_url,
        customer_email=str(getattr(current_user, "email", "") or tenant.billing_email or settings.billing_support_email),
        metadata={"tenant_slug": tenant.slug, "plan_key": normalized_plan_key, "billing_cycle": normalized_cycle},
    )
    _record_billing_event(
        db,
        tenant=tenant,
        provider="stripe",
        source="checkout",
        event_key="billing.checkout_started",
        status="processed",
        plan_key=normalized_plan_key,
        billing_cycle=normalized_cycle,
        external_event_id=str(getattr(checkout_session, "id", "") or ""),
        payload={"plan_key": normalized_plan_key, "billing_cycle": normalized_cycle},
        result={"url": getattr(checkout_session, "url", None)},
    )
    db.commit()
    return {
        "mode": "live",
        "configured": True,
        "billing_cycle": normalized_cycle,
        "url": getattr(checkout_session, "url", None),
        "message": "Stripe checkout session created.",
        "summary": get_billing_summary(db, current_user),
    }


def create_billing_portal_preview(db: Session, current_user: Any) -> dict[str, Any]:
    tenant = _resolve_tenant(db, current_user)
    subscription = ensure_subscription_record(db, tenant)
    client = _stripe_client()
    if client is None or subscription.provider != "stripe" or not subscription.external_customer_id:
        return {
            "available": False,
            "configured": bool(client),
            "url": None,
            "message": "Billing portal is unavailable until Stripe customer details are configured.",
        }
    session = client.billing_portal.Session.create(
        customer=subscription.external_customer_id,
        return_url=settings.billing_checkout_cancel_url,
    )
    return {"available": True, "configured": True, "url": getattr(session, "url", None), "message": "Billing portal ready."}


def _apply_checkout_completed_event(db: Session, *, tenant: Tenant, event_key: str, external_event_id: str | None, event_object: dict[str, Any]) -> dict[str, Any]:
    metadata = _coerce_metadata(event_object.get("metadata"))
    plan_key = _normalize_plan_key(metadata.get("plan_key") or tenant.plan_key)
    billing_cycle = str(metadata.get("billing_cycle") or "monthly").strip().lower() or "monthly"
    previous_plan_key = tenant.plan_key
    tenant.plan_key = plan_key
    subscription = ensure_subscription_record(db, tenant)
    subscription.provider = "stripe"
    subscription.status = "active"
    subscription.plan_key = plan_key
    subscription.external_customer_id = str(event_object.get("customer") or subscription.external_customer_id or "").strip() or None
    subscription.external_subscription_id = str(event_object.get("subscription") or subscription.external_subscription_id or "").strip() or None
    _update_subscription_metadata(
        subscription,
        managed_mode="stripe",
        billing_cycle=billing_cycle,
        last_synced_at=_utc_now().isoformat(),
        last_webhook_event_id=external_event_id,
    )
    sync_tenant_plan_entitlements(db, tenant)
    _record_billing_event(
        db,
        tenant=tenant,
        provider="stripe",
        source="webhook",
        event_key=event_key,
        status="processed",
        external_event_id=external_event_id,
        plan_key=plan_key,
        billing_cycle=billing_cycle,
        external_customer_id=subscription.external_customer_id,
        external_subscription_id=subscription.external_subscription_id,
        payload={"event_id": external_event_id, "type": event_key, "object": event_object},
        result={"handled": True, "tenant_slug": tenant.slug},
    )
    _emit_plan_change_side_effects(db, tenant, previous_plan_key=previous_plan_key, current_plan_key=plan_key, source="webhook")
    return {"handled": True, "duplicate": False, "tenant_slug": tenant.slug, "plan_key": plan_key}


def process_billing_webhook_event(db: Session, event: dict[str, Any]) -> dict[str, Any]:
    payload = _coerce_metadata(event)
    event_key = str(payload.get("type") or "billing.unknown").strip()
    external_event_id = str(payload.get("id") or "").strip() or None
    event_object = _coerce_metadata(_coerce_metadata(payload.get("data")).get("object") or payload.get("object"))
    metadata = _coerce_metadata(event_object.get("metadata"))
    tenant_slug = str(metadata.get("tenant_slug") or "").strip().lower() or None

    if external_event_id:
        existing = db.execute(
            select(BillingEventRecord)
            .where(BillingEventRecord.external_event_id == external_event_id)
            .order_by(BillingEventRecord.created_at.asc())
        ).scalars().first()
        if existing is not None:
            duplicate_row = _record_billing_event(
                db,
                tenant=existing.tenant,
                provider=existing.provider,
                source="webhook",
                event_key=event_key,
                status="duplicate",
                external_event_id=external_event_id,
                plan_key=existing.plan_key,
                billing_cycle=existing.billing_cycle,
                external_customer_id=existing.external_customer_id,
                external_subscription_id=existing.external_subscription_id,
                payload={"event_id": external_event_id, "type": event_key, "object": event_object},
                result={"handled": True, "duplicate": True},
            )
            db.commit()
            return {"handled": True, "duplicate": True, "event_id": duplicate_row.id}

    tenant = _resolve_tenant_by_slug(db, tenant_slug) if tenant_slug else None
    if tenant is None:
        _record_billing_event(
            db,
            tenant=None,
            provider="stripe",
            source="webhook",
            event_key=event_key,
            status="ignored",
            external_event_id=external_event_id,
            payload={"event_id": external_event_id, "type": event_key, "object": event_object},
            result={"handled": False, "reason": "unknown_tenant"},
        )
        db.commit()
        return {"handled": False, "duplicate": False, "reason": "unknown_tenant"}

    if event_key in {"checkout.session.completed", "customer.subscription.updated"}:
        result = _apply_checkout_completed_event(db, tenant=tenant, event_key=event_key, external_event_id=external_event_id, event_object=event_object)
        db.commit()
        return result

    _record_billing_event(
        db,
        tenant=tenant,
        provider="stripe",
        source="webhook",
        event_key=event_key,
        status="ignored",
        external_event_id=external_event_id,
        payload={"event_id": external_event_id, "type": event_key, "object": event_object},
        result={"handled": False, "reason": "unsupported_event"},
    )
    db.commit()
    return {"handled": False, "duplicate": False, "reason": "unsupported_event"}


def handle_billing_webhook_request(db: Session, body: bytes, stripe_signature: str | None = None) -> dict[str, Any]:
    if not body:
        raise ValidationError("Billing webhook body is empty.")
    client = _stripe_client()
    event: dict[str, Any]
    if client is not None and settings.stripe_webhook_secret and stripe_signature:
        event = client.Webhook.construct_event(body, stripe_signature, settings.stripe_webhook_secret)
    else:
        event = json.loads(body.decode("utf-8"))
    return process_billing_webhook_event(db, event)


def queue_billing_recovery_action(db: Session, current_user: Any, *, action: str) -> dict[str, Any]:
    from backend.services.job_queue_service import enqueue_billing_reconciliation

    normalized_action = str(action or "").strip().lower()
    if normalized_action not in _BILLING_RECOVERY_ACTIONS:
        raise ValidationError("Unsupported billing recovery action.")
    tenant = _resolve_tenant(db, current_user)
    subscription = ensure_subscription_record(db, tenant)
    event_snapshot = _build_billing_event_snapshot(db, tenant)
    failed_event_id = event_snapshot.get("latest_failed_event_id") if normalized_action == "retry_last_failure" else None
    if normalized_action == "retry_last_failure" and not failed_event_id:
        raise ValidationError("No failed billing event is available to replay.")
    job = enqueue_billing_reconciliation(
        db,
        tenant=tenant,
        action=normalized_action,
        failed_event_id=failed_event_id,
    )
    _write_billing_recovery_state(
        subscription,
        last_recovery_action=normalized_action,
        last_recovery_status="queued",
        last_recovery_error=None,
    )
    db.commit()
    return {
        "queued": True,
        "job_id": job.id,
        "action": normalized_action,
        "message": f"Billing recovery action queued: {normalized_action.replace('_', ' ')}.",
        "summary": get_billing_summary(db, current_user),
    }


def process_billing_recovery_job(
    db: Session,
    *,
    tenant_id: str,
    action: str,
    failed_event_id: str | None = None,
    job_id: str | None = None,
) -> dict[str, Any]:
    tenant = db.get(Tenant, tenant_id)
    if tenant is None:
        raise NotFoundError("The billing recovery tenant no longer exists.")
    subscription = ensure_subscription_record(db, tenant)
    normalized_action = str(action or "").strip().lower()
    if normalized_action not in _BILLING_RECOVERY_ACTIONS:
        raise ValidationError("Unsupported billing recovery action.")

    now_iso = _utc_now().isoformat()
    history_item: dict[str, Any]
    if normalized_action == "retry_last_failure":
        failed_row = db.get(BillingEventRecord, failed_event_id) if failed_event_id else None
        if failed_row is None or failed_row.tenant_id != tenant.id:
            failed_row = db.execute(
                select(BillingEventRecord)
                .where(BillingEventRecord.tenant_id == tenant.id, BillingEventRecord.status == "failed")
                .order_by(BillingEventRecord.received_at.desc())
            ).scalars().first()
        if failed_row is None:
            raise ValidationError("No failed billing event is available to replay.")
        payload = _coerce_metadata(failed_row.payload_json)
        event_object = _coerce_metadata(payload.get("object"))
        if not event_object:
            raise ValidationError("Failed billing event does not contain a replayable payload.")
        metadata = _coerce_metadata(event_object.get("metadata"))
        plan_key = _normalize_plan_key(metadata.get("plan_key") or failed_row.plan_key or tenant.plan_key)
        previous_plan_key = tenant.plan_key
        tenant.plan_key = plan_key
        subscription.provider = "stripe"
        subscription.status = "active"
        subscription.plan_key = plan_key
        subscription.external_customer_id = str(event_object.get("customer") or failed_row.external_customer_id or subscription.external_customer_id or "").strip() or None
        subscription.external_subscription_id = str(event_object.get("subscription") or failed_row.external_subscription_id or subscription.external_subscription_id or "").strip() or None
        _update_subscription_metadata(
            subscription,
            managed_mode="stripe",
            billing_cycle=str(metadata.get("billing_cycle") or failed_row.billing_cycle or "monthly").strip().lower() or "monthly",
            last_synced_at=now_iso,
            last_webhook_event_id=failed_row.external_event_id,
        )
        sync_tenant_plan_entitlements(db, tenant)
        failed_row.status = "replayed"
        failed_row.processed_at = _utc_now()
        failed_row.result_json = {**_coerce_metadata(failed_row.result_json), "replayed_at": now_iso, "job_id": job_id}
        _record_billing_event(
            db,
            tenant=tenant,
            provider="stripe",
            source="recovery",
            event_key="billing.recovery_replayed",
            status="processed",
            external_event_id=None,
            plan_key=plan_key,
            billing_cycle=str(metadata.get("billing_cycle") or failed_row.billing_cycle or "monthly").strip().lower() or "monthly",
            external_customer_id=subscription.external_customer_id,
            external_subscription_id=subscription.external_subscription_id,
            payload={"failed_event_id": failed_row.id, "event_key": failed_row.event_key},
            result={"job_id": job_id, "replayed_from_event_id": failed_row.id},
        )
        _emit_plan_change_side_effects(db, tenant, previous_plan_key=previous_plan_key, current_plan_key=plan_key, source="recovery_replay")
        history_item = {
            "kind": "replay",
            "action": normalized_action,
            "status": "succeeded",
            "created_at": now_iso,
            "failed_event_id": failed_row.id,
            "job_id": job_id,
        }
    elif normalized_action == "sync_entitlements":
        sync_tenant_plan_entitlements(db, tenant)
        history_item = {"kind": "sync", "action": normalized_action, "status": "succeeded", "created_at": now_iso, "job_id": job_id}
    else:
        _update_subscription_metadata(subscription, last_reconciled_at=now_iso)
        history_item = {"kind": "reconcile", "action": normalized_action, "status": "succeeded", "created_at": now_iso, "job_id": job_id}

    _write_billing_recovery_state(
        subscription,
        last_reconciled_at=now_iso if normalized_action == "reconcile" else _read_billing_recovery_state(subscription).get("last_reconciled_at"),
        last_recovery_action=normalized_action,
        last_recovery_status="succeeded",
        last_recovery_error=None,
        history_item=history_item,
    )
    db.flush()
    return {"ok": True, "action": normalized_action, "tenant_slug": tenant.slug, "job_id": job_id}


def get_billing_ops_snapshot(
    *,
    db: Session | None = None,
    tenant_slug: str | None = None,
    current_user: Any | None = None,
) -> dict[str, Any]:
    owns_session = db is None
    session = db or SessionLocal()
    try:
        tenant = _resolve_tenant(session, current_user) if current_user is not None else _resolve_tenant_by_slug(session, tenant_slug)
        subscription = ensure_subscription_record(session, tenant)
        sync_tenant_plan_entitlements(session, tenant)
        event_snapshot = _build_billing_event_snapshot(session, tenant)
        recovery_snapshot = _build_billing_recovery_snapshot(session, tenant, subscription, event_snapshot)
        sync_snapshot = _build_billing_sync_snapshot(subscription, event_snapshot, recovery_snapshot)
        snapshot = _build_billing_ops_snapshot(session, tenant, subscription, event_snapshot, recovery_snapshot, sync_snapshot)
    finally:
        if owns_session:
            session.close()
    return snapshot
