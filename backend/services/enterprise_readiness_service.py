from __future__ import annotations

import json
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[2]
VALIDATION_TRACKER_PATH = PROJECT_ROOT / "runtime-exports" / "strategy-validation" / "latest" / "validation_tracker.json"


def _normalize_enterprise_status(value: Any) -> str:
    status = str(value or "warning").strip().lower()
    if status in {"blocked", "fail", "error"}:
        return "blocked"
    if status in {"warning", "partial", "unknown", "pending", "attention"}:
        return "warning"
    return "ready"


def load_validation_tracker_snapshot(path: Path | None = None) -> dict[str, Any]:
    tracker_path = path or VALIDATION_TRACKER_PATH
    try:
        payload = json.loads(tracker_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {
            "available": False,
            "status": "warning",
            "label": "Validation tracker unavailable",
            "detail": "Strategy validation tracker has not been exported yet.",
            "next_action": "Run the strategy validation pack before calling the stack enterprise-ready.",
            "status_counts": {"pass": 0, "partial": 0, "fail": 0, "pending": 0},
            "settings_locked": False,
        }
    except json.JSONDecodeError:
        return {
            "available": False,
            "status": "blocked",
            "label": "Validation tracker invalid",
            "detail": "Strategy validation tracker could not be parsed.",
            "next_action": "Repair the validation tracker export before using it as a readiness gate.",
            "status_counts": {"pass": 0, "partial": 0, "fail": 0, "pending": 0},
            "settings_locked": False,
        }

    status = str(payload.get("overall_status") or "warning").strip().lower()
    status_counts = dict(payload.get("status_counts") or {})
    next_actions = list(payload.get("next_actions") or [])
    return {
        "available": True,
        "status": status,
        "label": f"Strategy validation {status}",
        "detail": f"{status_counts.get('fail', 0)} failed checks, {status_counts.get('partial', 0)} partial checks, {status_counts.get('pass', 0)} passed checks.",
        "next_action": next_actions[0] if next_actions else "Review the validation tracker before widening rollout.",
        "status_counts": {
            "pass": int(status_counts.get("pass", 0) or 0),
            "partial": int(status_counts.get("partial", 0) or 0),
            "fail": int(status_counts.get("fail", 0) or 0),
            "pending": int(status_counts.get("pending", 0) or 0),
        },
        "settings_locked": bool(payload.get("settings_locked", False)),
        "version": str(payload.get("version") or "").strip() or None,
        "generated_at": payload.get("generated_at"),
    }


def build_enterprise_readiness_snapshot(
    *,
    readiness_snapshot: dict[str, Any],
    deployment_snapshot: dict[str, Any],
    launch_rollup: dict[str, Any],
    order_lifecycle: dict[str, Any],
    validation_tracker: dict[str, Any],
) -> dict[str, Any]:
    checks = [
        {
            "key": "production",
            "label": "Production readiness",
            "status": str(readiness_snapshot.get("summary", {}).get("status") or "warning").strip().lower(),
            "detail": str(readiness_snapshot.get("summary", {}).get("next_action") or "Production readiness needs review."),
        },
        {
            "key": "deployment",
            "label": "Deployment readiness",
            "status": str(deployment_snapshot.get("summary", {}).get("status") or "warning").strip().lower(),
            "detail": str(deployment_snapshot.get("summary", {}).get("next_action") or "Deployment readiness needs review."),
        },
        {
            "key": "tenant_launch",
            "label": "Tenant launch",
            "status": str(launch_rollup.get("summary", {}).get("status") or "warning").strip().lower(),
            "detail": str(launch_rollup.get("summary", {}).get("next_action") or "Tenant launch readiness needs review."),
        },
        {
            "key": "order_lifecycle",
            "label": "Order lifecycle",
            "status": str(order_lifecycle.get("summary", {}).get("status") or "warning").strip().lower(),
            "detail": str(order_lifecycle.get("summary", {}).get("message") or "Order lifecycle health needs review."),
        },
        {
            "key": "strategy_validation",
            "label": "Strategy validation",
            "status": str(validation_tracker.get("status") or "warning").strip().lower(),
            "detail": str(validation_tracker.get("next_action") or validation_tracker.get("detail") or "Strategy validation needs review."),
        },
    ]

    normalized_statuses = [_normalize_enterprise_status(item["status"]) for item in checks]

    if "blocked" in normalized_statuses:
        status = "blocked"
    elif "warning" in normalized_statuses:
        status = "warning"
    else:
        status = "ready"

    blockers = list(dict.fromkeys(item["detail"] for item, level in zip(checks, normalized_statuses) if level == "blocked"))
    warnings = list(dict.fromkeys(item["detail"] for item, level in zip(checks, normalized_statuses) if level == "warning"))
    ready_checks = sum(1 for level in normalized_statuses if level == "ready")
    warning_checks = sum(1 for level in normalized_statuses if level == "warning")
    blocked_checks = sum(1 for level in normalized_statuses if level == "blocked")
    total_checks = len(checks)
    weighted_score = ready_checks + (warning_checks * 0.5)
    readiness_percent = round((weighted_score / max(total_checks, 1)) * 100, 1)
    next_action = blockers[0] if blockers else warnings[0] if warnings else "Enterprise readiness checks are clear."

    return {
        "summary": {
            "status": status,
            "ready": status == "ready",
            "ready_checks": ready_checks,
            "warning_checks": warning_checks,
            "blocked_checks": blocked_checks,
            "total_checks": total_checks,
            "readiness_percent": readiness_percent,
            "blockers": blockers,
            "warnings": warnings,
            "next_action": next_action,
        },
        "checks": checks,
        "validation_tracker": validation_tracker,
    }
