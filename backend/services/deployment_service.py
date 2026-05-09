from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from backend.core.config import settings

PROJECT_ROOT = Path(__file__).resolve().parents[2]

DEPLOYMENT_ARTIFACTS: tuple[tuple[str, str], ...] = (
    ("docker-compose.yml", "Docker Compose stack"),
    ("backend/Dockerfile", "Backend container build"),
    ("frontend/Dockerfile", "Frontend container build"),
    (".env.example", "Environment template"),
    ("Makefile", "Local operator commands"),
)

RUNBOOK_FILES: tuple[tuple[str, str], ...] = (
    ("docs/runbooks/deployment.md", "Deployment runbook"),
    ("docs/runbooks/backup_restore.md", "Backup and restore runbook"),
    ("docs/runbooks/incident_response.md", "Incident response runbook"),
    ("docs/runbooks/rollback.md", "Rollback runbook"),
    ("docs/runbooks/slow_app.md", "Slow app triage runbook"),
    ("docs/runbooks/stale_feed.md", "Stale market-data runbook"),
    ("docs/runbooks/backlog_recovery.md", "Async backlog recovery runbook"),
    ("docs/runbooks/own_account_intraday_implementation_checklist.md", "Own-account intraday checklist"),
)

BACKUP_STATUS_PATH = Path("runtime-logs/backup-status.json")
TRADE_AUTOMATION_READINESS_STATUS_PATH = Path("runtime-logs/trade-automation-readiness.json")
TRADE_AUTOMATION_ROUTE_LATENCY_TARGET_MS = 5000.0
BACKUP_MANIFEST_REQUIRED_FIELDS: tuple[tuple[str, str], ...] = (
    ("provider", "Backup provider"),
    ("schedule", "Backup schedule"),
    ("retention_days", "Retention days"),
    ("location", "Backup location"),
)
_DEFAULT_AUTH_SESSION_SECRET = "stocksignals-local-session-secret"
_DEFAULT_AUTH_STATE_SECRET = "stocksignals-local-auth-state-secret"
_DEFAULT_API_TOKEN_SALT = "stocksignals-local-token-salt"
_LOCAL_DATABASE_PREFIXES = ("sqlite:///", "sqlite+pysqlite:///")
_OPERATOR_LOCAL_PROFILES = {"operator-local", "desktop", "internal-demo"}

DEFAULT_BACKUP_STATUS = {
    "status": "attention",
    "provider": "local-volume-snapshot",
    "schedule": "Before each deployment and nightly when automation is enabled",
    "last_success_at": None,
    "last_attempt_at": None,
    "restore_tested_at": None,
    "retention_days": 14,
    "location": "backend-storage volume and runtime-logs/",
    "notes": "Seed manifest for local and staging operations. Update this after the first verified backup and restore drill.",
}


def _isoformat_utc(value: float) -> str:
    return datetime.fromtimestamp(value, tz=timezone.utc).isoformat()


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_manifest_timestamp(value: Any) -> datetime | None:
    normalized = str(value or "").strip()
    if not normalized:
        return None
    candidate = normalized.replace("Z", "+00:00")
    parsed = datetime.fromisoformat(candidate)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _build_file_snapshot(project_root: Path, relative_path: str, label: str) -> dict[str, Any]:
    file_path = project_root / relative_path
    exists = file_path.exists()
    snapshot = {
        "path": relative_path,
        "label": label,
        "exists": exists,
        "status": "ready" if exists else "missing",
        "modified_at": _isoformat_utc(file_path.stat().st_mtime) if exists else None,
    }
    if not exists:
        snapshot["note"] = "Create or restore this file before the release path is considered ready."
    return snapshot


def _environment_check(
    key: str,
    label: str,
    *,
    status: str,
    message: str,
    blocking: bool = False,
) -> dict[str, Any]:
    return {
        "key": key,
        "label": label,
        "status": status,
        "ready": status == "ready",
        "blocking": blocking,
        "message": message,
    }


def _build_environment_snapshot() -> dict[str, Any]:
    checks: list[dict[str, Any]] = []
    environment = str(getattr(settings, "environment", "development") or "development").strip().lower()
    runtime_profile = str(getattr(settings, "enterprise_runtime_profile", "production") or "production").strip().lower()
    reload_enabled = bool(getattr(settings, "reload", True))
    demo_auth_enabled = bool(getattr(settings, "allow_demo_auth", False))
    auth_enabled = bool(getattr(settings, "auth_enabled", False))
    auth_provider = str(getattr(settings, "auth_provider", "local-demo") or "local-demo").strip().lower()
    database_url = str(getattr(settings, "database_url", "") or "").strip()
    auth_session_secret = str(getattr(settings, "auth_session_secret", _DEFAULT_AUTH_SESSION_SECRET) or "").strip()
    auth_state_secret = str(getattr(settings, "auth_state_secret", _DEFAULT_AUTH_STATE_SECRET) or "").strip()
    api_token_salt = str(getattr(settings, "api_token_salt", _DEFAULT_API_TOKEN_SALT) or "").strip()
    market_data_provider = str(getattr(settings, "market_data_provider", "free_delayed") or "free_delayed").strip().lower()
    alpaca_api_key_id = str(getattr(settings, "alpaca_api_key_id", "") or "").strip()
    alpaca_api_secret_key = str(getattr(settings, "alpaca_api_secret_key", "") or "").strip()
    stripe_publishable_key = str(getattr(settings, "stripe_publishable_key", "") or "").strip()
    stripe_secret_key = str(getattr(settings, "stripe_secret_key", "") or "").strip()
    stripe_webhook_secret = str(getattr(settings, "stripe_webhook_secret", "") or "").strip()
    operator_local_mode = runtime_profile in _OPERATOR_LOCAL_PROFILES

    checks.append(
        _environment_check(
            "app_env",
            "Production environment",
            status="ready" if environment not in {"development", "local", "test"} else "blocked",
            message=(
                f"APP_ENV is set to {environment}."
                if environment not in {"development", "local", "test"}
                else "APP_ENV is still set to a development-style environment."
            ),
            blocking=environment in {"development", "local", "test"},
        )
    )
    checks.append(
        _environment_check(
            "api_reload",
            "Hot reload disabled",
            status="ready" if not reload_enabled else "blocked",
            message="API reload is disabled for production use." if not reload_enabled else "API reload is still enabled.",
            blocking=reload_enabled,
        )
    )
    checks.append(
        _environment_check(
            "demo_auth",
            "Demo auth disabled",
            status="ready" if (not demo_auth_enabled or operator_local_mode) else "blocked",
            message=(
                "Demo auth is disabled."
                if not demo_auth_enabled
                else "Demo auth is enabled for operator-local mode."
                if operator_local_mode
                else "ALLOW_DEMO_AUTH is still enabled."
            ),
            blocking=bool(demo_auth_enabled and not operator_local_mode),
        )
    )
    auth_ready = auth_enabled and auth_provider != "local-demo"
    checks.append(
        _environment_check(
            "auth_provider",
            "Production auth enabled",
            status="ready" if (auth_ready or operator_local_mode) else "blocked",
            message=(
                f"Auth is enabled with provider {auth_provider}."
                if auth_ready
                else "Local-demo auth is active for operator-local mode."
                if operator_local_mode
                else "AUTH_ENABLED or AUTH_PROVIDER is still using a local/demo auth path."
            ),
            blocking=bool((not auth_ready) and not operator_local_mode),
        )
    )
    database_ready = bool(database_url) and not database_url.startswith(_LOCAL_DATABASE_PREFIXES)
    checks.append(
        _environment_check(
            "database_url",
            "Production database",
            status="ready" if (database_ready or operator_local_mode) else "blocked",
            message=(
                "Database URL points at a non-local production candidate."
                if database_ready
                else "Local SQLite is active for operator-local mode; keep backup and restore drills current."
                if operator_local_mode
                else "DATABASE_URL still points at a local SQLite database."
            ),
            blocking=bool((not database_ready) and not operator_local_mode),
        )
    )
    checks.append(
        _environment_check(
            "auth_session_secret",
            "Session secret rotated",
            status="ready" if auth_session_secret and auth_session_secret != _DEFAULT_AUTH_SESSION_SECRET else "blocked",
            message="AUTH_SESSION_SECRET is customized." if auth_session_secret and auth_session_secret != _DEFAULT_AUTH_SESSION_SECRET else "AUTH_SESSION_SECRET is still using the local default.",
            blocking=not (auth_session_secret and auth_session_secret != _DEFAULT_AUTH_SESSION_SECRET),
        )
    )
    checks.append(
        _environment_check(
            "auth_state_secret",
            "Auth state secret rotated",
            status="ready" if auth_state_secret and auth_state_secret != _DEFAULT_AUTH_STATE_SECRET else "blocked",
            message="AUTH_STATE_SECRET is customized." if auth_state_secret and auth_state_secret != _DEFAULT_AUTH_STATE_SECRET else "AUTH_STATE_SECRET is still using the local default.",
            blocking=not (auth_state_secret and auth_state_secret != _DEFAULT_AUTH_STATE_SECRET),
        )
    )
    checks.append(
        _environment_check(
            "api_token_salt",
            "API token salt rotated",
            status="ready" if api_token_salt and api_token_salt != _DEFAULT_API_TOKEN_SALT else "blocked",
            message="API token salt is customized." if api_token_salt and api_token_salt != _DEFAULT_API_TOKEN_SALT else "API token salt is still using the local default.",
            blocking=not (api_token_salt and api_token_salt != _DEFAULT_API_TOKEN_SALT),
        )
    )
    free_delayed_market_data = market_data_provider in {"free_delayed", "yfinance", "delayed"}
    market_ready = free_delayed_market_data or market_data_provider != "alpaca" or (alpaca_api_key_id and alpaca_api_secret_key)
    checks.append(
        _environment_check(
            "market_data",
            "Market-data credentials",
            status="ready" if market_ready else "blocked",
            message=(
                "Free/delayed market data is the active internal-paper research lane."
                if free_delayed_market_data
                else f"{market_data_provider} credentials are configured."
                if market_ready
                else "Alpaca API credentials are not configured."
            ),
            blocking=not market_ready,
        )
    )
    stripe_values = [bool(stripe_publishable_key), bool(stripe_secret_key), bool(stripe_webhook_secret)]
    if all(stripe_values):
        stripe_status = "ready"
        stripe_message = "Stripe publishable, secret, and webhook keys are configured."
        stripe_blocking = False
    elif any(stripe_values):
        stripe_status = "warning" if operator_local_mode else "blocked"
        stripe_message = (
            "Stripe billing keys are only partially configured for operator-local mode."
            if operator_local_mode
            else "Stripe billing keys are only partially configured."
        )
        stripe_blocking = not operator_local_mode
    else:
        stripe_status = "ready" if operator_local_mode else "warning"
        stripe_message = (
            "Stripe billing keys are not required for operator-local mode."
            if operator_local_mode
            else "Stripe billing keys are not configured yet."
        )
        stripe_blocking = False
    checks.append(
        _environment_check(
            "stripe",
            "Stripe billing credentials",
            status=stripe_status,
            message=stripe_message,
            blocking=stripe_blocking,
        )
    )

    blockers = [item["message"] for item in checks if item["status"] == "blocked" and item.get("blocking")]
    warnings = [item["message"] for item in checks if item["status"] == "warning" or (item["status"] == "blocked" and not item.get("blocking"))]
    status = "blocked" if blockers else "warning" if warnings else "ready"
    next_action = blockers[0] if blockers else warnings[0] if warnings else "Production environment settings are configured."

    return {
        "summary": {
            "status": status,
            "ready_checks": sum(1 for item in checks if item["status"] == "ready"),
            "total_checks": len(checks),
            "blockers": blockers,
            "warnings": warnings,
            "next_action": next_action,
        },
        "checks": checks,
    }


def _is_operator_local_profile() -> bool:
    runtime_profile = str(getattr(settings, "enterprise_runtime_profile", "production") or "production").strip().lower()
    return runtime_profile in _OPERATOR_LOCAL_PROFILES


def _load_backup_status(project_root: Path) -> tuple[dict[str, Any], bool]:
    backup_path = project_root / BACKUP_STATUS_PATH
    if not backup_path.exists():
        status = dict(DEFAULT_BACKUP_STATUS)
        status["manifest_path"] = str(BACKUP_STATUS_PATH).replace("\\", "/")
        status["configured"] = False
        status["needs_attention"] = True
        status["restore_warning_days"] = int(settings.backup_restore_warning_days)
        status["restore_age_days"] = None
        status["warnings"] = []
        status["validation"] = {
            "valid": False,
            "issue_count": 1,
            "issues": ["Backup manifest file is missing."],
        }
        status["checklist"] = [
            {"key": "manifest", "label": "Backup manifest recorded", "ready": False},
            {"key": "manifest_valid", "label": "Backup manifest valid", "ready": False},
            {"key": "success", "label": "Successful backup captured", "ready": False},
            {"key": "restore", "label": "Restore drill recorded", "ready": False},
        ]
        return status, False

    try:
        raw_status = json.loads(backup_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        raw_status = dict(DEFAULT_BACKUP_STATUS)
        raw_status["status"] = "error"
        raw_status["notes"] = "Backup manifest exists but could not be parsed."

    status = {**DEFAULT_BACKUP_STATUS, **raw_status}
    status["manifest_path"] = str(BACKUP_STATUS_PATH).replace("\\", "/")
    status["configured"] = True
    warnings: list[str] = []
    validation_issues: list[str] = []

    for field_key, field_label in BACKUP_MANIFEST_REQUIRED_FIELDS:
        value = status.get(field_key)
        if field_key == "retention_days":
            try:
                numeric_value = int(value)
            except (TypeError, ValueError):
                numeric_value = 0
            status[field_key] = numeric_value
            if numeric_value <= 0:
                validation_issues.append(f"{field_label} must be a positive integer in the backup manifest.")
            continue
        if not str(value or "").strip():
            validation_issues.append(f"{field_label} is missing from the backup manifest.")

    parsed_last_success_at = None
    parsed_last_attempt_at = None
    parsed_restore_tested_at = None
    for field_key, field_label in (
        ("last_success_at", "Last successful backup timestamp"),
        ("last_attempt_at", "Last backup attempt timestamp"),
        ("restore_tested_at", "Restore drill timestamp"),
    ):
        raw_value = status.get(field_key)
        if raw_value in (None, ""):
            continue
        try:
            parsed_value = _parse_manifest_timestamp(raw_value)
        except ValueError:
            validation_issues.append(f"{field_label} must be an ISO-8601 timestamp in the backup manifest.")
            continue
        status[field_key] = parsed_value.isoformat()
        if field_key == "last_success_at":
            parsed_last_success_at = parsed_value
        elif field_key == "last_attempt_at":
            parsed_last_attempt_at = parsed_value
        elif field_key == "restore_tested_at":
            parsed_restore_tested_at = parsed_value

    restore_warning_days = max(1, int(settings.backup_restore_warning_days))
    restore_age_days = None
    if parsed_restore_tested_at is not None:
        restore_age_days = round((_utc_now() - parsed_restore_tested_at).total_seconds() / 86400, 1)
        if restore_age_days > restore_warning_days:
            warnings.append(
                f"Restore drill is {restore_age_days} days old and should be rerun before the next pilot release."
            )

    manifest_valid = not validation_issues
    if status.get("status") == "error" and "could not be parsed" in str(status.get("notes") or "").lower():
        validation_issues.append("Backup manifest JSON could not be parsed.")
        manifest_valid = False

    status["restore_warning_days"] = restore_warning_days
    status["restore_age_days"] = restore_age_days
    status["warnings"] = warnings
    status["validation"] = {
        "valid": manifest_valid,
        "issue_count": len(validation_issues),
        "issues": validation_issues,
    }
    status["checklist"] = [
        {"key": "manifest", "label": "Backup manifest recorded", "ready": True},
        {"key": "manifest_valid", "label": "Backup manifest valid", "ready": manifest_valid},
        {"key": "success", "label": "Successful backup captured", "ready": bool(parsed_last_success_at or status.get("last_success_at"))},
        {"key": "restore", "label": "Restore drill recorded", "ready": bool(parsed_restore_tested_at or status.get("restore_tested_at"))},
    ]
    status["needs_attention"] = any(not item["ready"] for item in status["checklist"]) or bool(warnings)
    if not manifest_valid:
        status["status"] = "error"
    elif warnings and str(status.get("status") or "").strip().lower() == "ready":
        status["status"] = "warning"
    return status, True


def _load_trade_automation_route_status(project_root: Path) -> dict[str, Any]:
    status_path = project_root / TRADE_AUTOMATION_READINESS_STATUS_PATH
    if not status_path.exists():
        return {
            "status": "missing",
            "ready": False,
            "checked_at": None,
            "status_path": str(TRADE_AUTOMATION_READINESS_STATUS_PATH).replace("\\", "/"),
            "latency_ms": None,
            "target_latency_ms": TRADE_AUTOMATION_ROUTE_LATENCY_TARGET_MS,
            "blockers": ["Trade Automation readiness smoke has not been recorded."],
            "warnings": [],
            "checklist": [
                {"key": "runtime_status_recorded", "label": "Runtime readiness status recorded", "ready": False},
                {"key": "trade_automation_route", "label": "Trade Automation route loads", "ready": False},
                {"key": "trade_automation_latency", "label": "Trade Automation route inside latency target", "ready": False},
            ],
        }
    try:
        raw = json.loads(status_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {
            "status": "error",
            "ready": False,
            "checked_at": None,
            "status_path": str(TRADE_AUTOMATION_READINESS_STATUS_PATH).replace("\\", "/"),
            "latency_ms": None,
            "target_latency_ms": TRADE_AUTOMATION_ROUTE_LATENCY_TARGET_MS,
            "blockers": ["Trade Automation readiness smoke status could not be parsed."],
            "warnings": [],
            "checklist": [
                {"key": "runtime_status_recorded", "label": "Runtime readiness status recorded", "ready": False},
                {"key": "trade_automation_route", "label": "Trade Automation route loads", "ready": False},
                {"key": "trade_automation_latency", "label": "Trade Automation route inside latency target", "ready": False},
            ],
        }

    status = dict(raw)
    blockers = [str(item) for item in list(status.get("blockers") or []) if str(item).strip()]
    warnings = [str(item) for item in list(status.get("warnings") or []) if str(item).strip()]
    route_ok = bool(status.get("trade_automation_ready")) and int(status.get("trade_automation_status_code") or 0) < 400
    latency_ms = status.get("trade_automation_latency_ms")
    try:
        latency_value = float(latency_ms)
    except (TypeError, ValueError):
        latency_value = None
    latency_ok = latency_value is not None and latency_value <= TRADE_AUTOMATION_ROUTE_LATENCY_TARGET_MS
    backend_ok = bool(status.get("backend_health_ok"))
    frontend_ok = bool(status.get("frontend_health_ok", True))

    if not backend_ok:
        blockers.append("Backend health check failed during readiness smoke.")
    if not route_ok:
        blockers.append("Trade Automation route failed during readiness smoke.")
    if latency_value is None:
        blockers.append("Trade Automation route latency was not measured.")
    elif not latency_ok:
        blockers.append(
            f"Trade Automation route latency {latency_value:.0f}ms exceeds target {TRADE_AUTOMATION_ROUTE_LATENCY_TARGET_MS:.0f}ms."
        )
    if not frontend_ok:
        warnings.append("Frontend health check failed or was unreachable during readiness smoke.")

    status["status_path"] = str(TRADE_AUTOMATION_READINESS_STATUS_PATH).replace("\\", "/")
    status["target_latency_ms"] = TRADE_AUTOMATION_ROUTE_LATENCY_TARGET_MS
    status["latency_ms"] = latency_value
    status["ready"] = not blockers
    status["status"] = "ready" if not blockers and not warnings else "warning" if not blockers else "blocked"
    status["blockers"] = blockers
    status["warnings"] = warnings
    status["checklist"] = [
        {"key": "runtime_status_recorded", "label": "Runtime readiness status recorded", "ready": True},
        {"key": "backend_health", "label": "Backend health is OK", "ready": backend_ok},
        {"key": "frontend_health", "label": "Frontend health is reachable", "ready": frontend_ok},
        {"key": "trade_automation_route", "label": "Trade Automation route loads", "ready": route_ok},
        {"key": "trade_automation_latency", "label": "Trade Automation route inside latency target", "ready": latency_ok},
    ]
    return status


def get_deployment_readiness_snapshot(project_root: Path | None = None) -> dict[str, Any]:
    root = project_root or PROJECT_ROOT
    deployment_items = [_build_file_snapshot(root, path, label) for path, label in DEPLOYMENT_ARTIFACTS]
    runbook_items = [_build_file_snapshot(root, path, label) for path, label in RUNBOOK_FILES]
    backup_status, backup_manifest_exists = _load_backup_status(root)
    trade_automation_route_status = _load_trade_automation_route_status(root)
    environment_snapshot = _build_environment_snapshot()

    backup_checks = backup_status.get("checklist", [])
    trade_automation_checks = trade_automation_route_status.get("checklist", [])
    environment_checks = list(environment_snapshot.get("checks") or [])
    total_checks = (
        len(deployment_items)
        + len(runbook_items)
        + len(backup_checks)
        + len(trade_automation_checks)
        + len(environment_checks)
    )
    ready_checks = sum(1 for item in deployment_items if item["exists"])
    ready_checks += sum(1 for item in runbook_items if item["exists"])
    ready_checks += sum(1 for item in backup_checks if item["ready"])
    ready_checks += sum(1 for item in trade_automation_checks if item["ready"])
    ready_checks += sum(1 for item in environment_checks if item.get("ready"))

    blockers: list[str] = []
    warnings: list[str] = list(backup_status.get("warnings") or [])

    def add_backup_issue(message: str) -> None:
        blockers.append(message)

    if any(not item["exists"] for item in deployment_items):
        blockers.append("Deployment artifacts are incomplete.")
    if any(not item["exists"] for item in runbook_items):
        blockers.append("Operator runbooks are missing or incomplete.")
    if not backup_manifest_exists:
        add_backup_issue("Backup manifest has not been created.")
    elif not bool((backup_status.get("validation") or {}).get("valid", False)):
        add_backup_issue("Backup manifest is invalid and should be corrected before pilot launch.")
    elif not backup_status.get("last_success_at"):
        add_backup_issue("No successful backup has been recorded yet.")
    if not backup_status.get("restore_tested_at"):
        add_backup_issue("Restore drill has not been recorded yet.")
    blockers.extend(trade_automation_route_status.get("blockers", []))
    warnings.extend(trade_automation_route_status.get("warnings", []))
    blockers.extend(environment_snapshot.get("summary", {}).get("blockers", []))
    warnings.extend(environment_snapshot.get("summary", {}).get("warnings", []))

    status = "attention" if blockers else "warning" if warnings else "ready"
    next_action = blockers[0] if blockers else warnings[0] if warnings else "Deployment path, backup posture, and runbooks are all ready."

    return {
        "summary": {
            "status": status,
            "readiness_percent": round((ready_checks / max(total_checks, 1)) * 100, 1),
            "ready_checks": ready_checks,
            "total_checks": total_checks,
            "blockers": blockers,
            "warnings": warnings,
            "next_action": next_action,
        },
        "deployment": {
            "items": deployment_items,
            "count": len(deployment_items),
            "ready_count": sum(1 for item in deployment_items if item["exists"]),
            "next_action": "Create any missing deployment artifacts before promoting the stack."
            if any(not item["exists"] for item in deployment_items)
            else "Deployment artifacts are present.",
        },
        "backups": backup_status,
        "environment": environment_snapshot,
        "trade_automation_route_status": trade_automation_route_status,
        "runbooks": {
            "items": runbook_items,
            "count": len(runbook_items),
            "ready_count": sum(1 for item in runbook_items if item["exists"]),
            "next_action": "Finish the missing operator runbooks."
            if any(not item["exists"] for item in runbook_items)
            else "Runbooks are available for deployment, rollback, backups, incident response, slow app, stale feed, backlog recovery, and Phase A go-live.",
        },
    }
