from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from backend.services.serialization import serialize_value


REPO_ROOT = Path(__file__).resolve().parents[2]
OPTIONS_VALIDATION_EXPORTS_DIR = REPO_ROOT / "runtime-exports" / "options-validation"


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _safe_json(value: Any) -> Any:
    return serialize_value(value)


def build_options_validation_summary(snapshot: dict[str, Any]) -> dict[str, Any]:
    validation = dict(snapshot.get("validation_artifact") or {})
    lifecycle = dict(snapshot.get("lifecycle") or {})
    return {
        "generated_at": _utc_now().isoformat(),
        "validation_scope": str(validation.get("validation_scope") or "personal_paper"),
        "readiness_state": str(validation.get("readiness_state") or "collecting_lifecycle_evidence"),
        "readiness_label": str(validation.get("readiness_label") or "collecting lifecycle evidence"),
        "required_clean_cycles": int(validation.get("required_clean_cycles") or 5),
        "clean_cycle_count": int(validation.get("clean_cycle_count") or 0),
        "clean_entry_count": int(validation.get("clean_entry_count") or 0),
        "clean_exit_count": int(validation.get("clean_exit_count") or 0),
        "blocked_entry_count": int(validation.get("blocked_entry_count") or 0),
        "blocked_exit_count": int(validation.get("blocked_exit_count") or 0),
        "stale_quote_block_count": int(validation.get("stale_quote_block_count") or 0),
        "orphan_event_count": int(validation.get("orphan_event_count") or 0),
        "open_position_count": int(validation.get("open_position_count") or 0),
        "working_order_count": int(validation.get("working_order_count") or 0),
        "last_broker_sync_at": validation.get("last_broker_sync_at"),
        "last_clean_lifecycle_at": validation.get("last_clean_lifecycle_at"),
        "blockers": list(validation.get("blockers") or []),
        "next_step": validation.get("next_step"),
        "automation_profile_key": snapshot.get("automation_profile_key") or lifecycle.get("automation_profile_key"),
        "last_scheduled_cycle_at": snapshot.get("last_scheduled_cycle_at") or lifecycle.get("last_scheduled_cycle_at"),
        "latest_scan_run_id": snapshot.get("latest_scan_run_id"),
    }


def export_options_validation(snapshot: dict[str, Any], *, output_dir: Path | None = None) -> dict[str, Any]:
    destination = Path(output_dir) if output_dir is not None else (OPTIONS_VALIDATION_EXPORTS_DIR / "latest")
    destination.mkdir(parents=True, exist_ok=True)

    summary = build_options_validation_summary(snapshot)
    validation_artifact = {
        "generated_at": _utc_now().isoformat(),
        "summary": summary,
        "snapshot": _safe_json(snapshot),
        "validation_artifact": _safe_json(snapshot.get("validation_artifact") or {}),
    }

    options_paper_validation_path = destination / "options_paper_validation.json"
    summary_path = destination / "summary.json"
    options_paper_validation_path.write_text(
        json.dumps(_safe_json(validation_artifact), indent=2),
        encoding="utf-8",
    )
    summary_path.write_text(json.dumps(_safe_json(summary), indent=2), encoding="utf-8")
    return {
        "destination": str(destination),
        "options_paper_validation_path": str(options_paper_validation_path),
        "summary_path": str(summary_path),
        "summary": summary,
    }
