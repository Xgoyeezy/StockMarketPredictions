from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


SCRIPT_ORDER = [
    "check_live_staging.py",
    "run_staging_acceptance.py",
    "audit_non_chart_routes.py",
    "check_frontend_ui_preflight.py",
]


def project_root() -> Path:
    return Path(__file__).resolve().parent.parent


def run_json_script(script_name: str) -> tuple[int, dict[str, object]]:
    root = project_root()
    script_path = root / "scripts" / script_name
    command = [sys.executable, str(script_path)]
    if script_name == "run_staging_acceptance.py":
        command.append(str(root / ".env.staging"))
    elif script_name == "check_live_staging.py":
        command.append(str(root / ".env.staging"))

    result = subprocess.run(command, capture_output=True, text=True, cwd=root)
    payload: dict[str, object]
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError:
        payload = {
            "status": "blocked",
            "raw_stdout": result.stdout,
            "raw_stderr": result.stderr,
        }
    return result.returncode, payload


def main() -> int:
    checks: dict[str, object] = {}
    blockers: list[str] = []
    warnings: list[str] = []

    for script_name in SCRIPT_ORDER:
        code, payload = run_json_script(script_name)
        key = script_name.removesuffix(".py")
        checks[key] = payload
        if code != 0 or payload.get("status") not in {"ready", "warning"}:
            blockers.append(f"{script_name} did not report ready status.")
        for warning in payload.get("warnings", []) or []:
            warnings.append(f"{script_name}: {warning}")

    status = "ready" if not blockers else "blocked"
    payload = {
        "status": status,
        "checks": checks,
        "blockers": blockers,
        "warnings": warnings,
        "next_action": (
            "Open the frontend and run the non-chart UI acceptance checklist."
            if status == "ready"
            else "Resolve the blocking readiness checks before attempting the non-chart UI pass."
        ),
    }
    print(json.dumps(payload, indent=2))
    return 0 if status == "ready" else 1


if __name__ == "__main__":
    raise SystemExit(main())
