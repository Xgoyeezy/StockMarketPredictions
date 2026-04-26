from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


PROJECT_ROOT = Path(__file__).resolve().parents[1]
TEST_FILE = PROJECT_ROOT / "tests" / "test_backend_behaviors.py"
TEST_CLASS = "BackendBehaviorTests"
TEST_IMPORT_PATH = "tests.test_backend_behaviors.BackendBehaviorTests"


@dataclass(frozen=True)
class BackendTestGroup:
    slug: str
    title: str
    description: str


GROUPS: tuple[BackendTestGroup, ...] = (
    BackendTestGroup(
        slug="market-desk",
        title="Market and desk intelligence",
        description="Chart, forecast, compare, notes, watchlist, and desk-facing market behavior.",
    ),
    BackendTestGroup(
        slug="identity-platform",
        title="Identity and platform controls",
        description="Auth, tenant or organization state, onboarding, billing, entitlements, and platform controls.",
    ),
    BackendTestGroup(
        slug="ops-readiness",
        title="Operations and readiness",
        description="Metrics, diagnostics, probes, deployment readiness, and release-oriented checks.",
    ),
    BackendTestGroup(
        slug="execution-trade",
        title="Execution and trade lifecycle",
        description="Order routing, broker adapters, portfolio and trade journal flows, and automation guardrails.",
    ),
)

EXECUTION_TOKENS = (
    "order_lifecycle",
    "open_trade",
    "fill_pending_order",
    "sync_pending_orders",
    "replace_and_cancel_pending_order",
    "close_trade",
    "portfolio_snapshot",
    "trade_journal",
    "position_management",
    "trade_automation",
    "execution_registry",
    "alpaca_",
    "equity_position_preview",
    "build_alpaca_equity_order_payload",
    "trade_summary",
)

PLATFORM_TOKENS = (
    "auth",
    "oidc",
    "tenant",
    "member_",
    "api_token",
    "api_usage",
    "security_snapshot",
    "rate_limit_snapshot",
    "partner_webhook",
    "partner_webhooks",
    "onboarding",
    "workspace",
    "delivery_",
    "launch_",
    "resume_active",
    "billing_",
    "checkout",
    "stripe",
    "entitlement",
    "plan_",
    "_plan",
    "feature_flag",
    "white_label",
    "branding",
    "invitation",
    "role_update",
)

OPS_TOKENS = (
    "market_data_freshness",
    "ops_metrics",
    "operation_metrics",
    "route_profile",
    "upstream_metrics",
    "operations_status",
    "frontend_bootstrap",
    "production_readiness",
    "release_gate",
    "deployment_readiness",
    "job_metrics",
    "deployment_probe",
    "support_diagnostics",
    "phase_a_exit",
    "core_pilot_probe",
    "json_writes",
    "market_upstream_timeout",
    "compare_route_profile",
)


def _iter_test_names() -> list[str]:
    tree = ast.parse(TEST_FILE.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == TEST_CLASS:
            return [
                item.name
                for item in node.body
                if isinstance(item, ast.FunctionDef) and item.name.startswith("test_")
            ]
    raise RuntimeError(f"Could not locate {TEST_CLASS} in {TEST_FILE}")


def _contains_any(name: str, fragments: Iterable[str]) -> bool:
    return any(fragment in name for fragment in fragments)


def classify_test_name(name: str) -> str:
    if _contains_any(name, EXECUTION_TOKENS):
        return "execution-trade"
    if _contains_any(name, PLATFORM_TOKENS):
        return "identity-platform"
    if _contains_any(name, OPS_TOKENS):
        return "ops-readiness"
    return "market-desk"


def grouped_test_names() -> dict[str, list[str]]:
    buckets = {group.slug: [] for group in GROUPS}
    for name in _iter_test_names():
        buckets[classify_test_name(name)].append(name)
    return buckets


def validate_grouping() -> dict[str, list[str]]:
    buckets = grouped_test_names()
    names = _iter_test_names()
    grouped_names = [name for names_in_group in buckets.values() for name in names_in_group]

    if sorted(grouped_names) != sorted(names):
        raise RuntimeError("Backend test grouping failed to cover the full suite exactly once.")
    for group in GROUPS:
        if not buckets[group.slug]:
            raise RuntimeError(f"Backend test group is empty: {group.slug}")
    return buckets


def unittest_names_for_group(group_slug: str) -> list[str]:
    buckets = validate_grouping()
    if group_slug not in buckets:
        raise KeyError(group_slug)
    return [f"{TEST_IMPORT_PATH}.{name}" for name in buckets[group_slug]]
