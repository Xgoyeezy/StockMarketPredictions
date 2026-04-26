from __future__ import annotations

from pathlib import Path


def main() -> int:
    project_root = Path(__file__).resolve().parents[1]
    python_path = project_root / "backend" / ".venv" / "Scripts" / "python.exe"
    runtime_path = project_root / "scripts" / "manage_api_runtime.py"
    readiness_path = project_root / "scripts" / "check_options_paper_readiness.py"
    staging_ops_path = project_root / "scripts" / "staging_ops.ps1"

    print(".env.staging is the canonical local paper-options validation lane.")
    print()
    print("Use this staging runbook in order:")
    print()
    print(f"& '{staging_ops_path}' -Action db-up")
    print(f"& '{staging_ops_path}' -Action use-local-db")
    print(f"& '{staging_ops_path}' -Action env-check")
    print(f"& '{staging_ops_path}' -Action db-check")
    print(f"& '{python_path}' '{runtime_path}' start --env-file .env.staging")
    print(f"& '{python_path}' '{runtime_path}' status --env-file .env.staging")
    print(f"& '{python_path}' '{readiness_path}' .env.staging")
    print()
    print("Stop the staging runtime with:")
    print(f"& '{python_path}' '{runtime_path}' stop --env-file .env.staging")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
