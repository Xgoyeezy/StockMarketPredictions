from __future__ import annotations

import argparse
import re
from datetime import date
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SESSION_PATH = PROJECT_ROOT / "runtime-exports" / "non_chart_ui_latest_session.md"
RUNS_PATH = PROJECT_ROOT / "runtime-exports" / "non_chart_ui_runs.md"


def _extract(pattern: str, text: str, default: str = "unknown") -> str:
    match = re.search(pattern, text, re.MULTILINE)
    return match.group(1).strip() if match else default


def _extract_all_routes(text: str) -> list[str]:
    return re.findall(r"- (?:Watchlist|Workspaces|Settings|Release): `([^`]+)`", text)


def _build_entry(session_text: str, result: str, first_failed_step: str, blockers: str, notes: str) -> str:
    frontend = _extract(r"- Frontend: `([^`]+)`", session_text)
    api = _extract(r"- API: `([^`]+)`", session_text)
    tenant_slug = _extract(r"- Tenant slug: `([^`]+)`", session_text)
    email = _extract(r"- Email: `([^`]+)`", session_text)
    plan = _extract(r"- Plan: `([^`]+)`", session_text)
    routes = _extract_all_routes(session_text)
    route_note = ", ".join(routes) if routes else "non-chart routes under validation"

    blocker_lines = [line.strip() for line in blockers.split("|") if line.strip()]
    note_lines = [line.strip() for line in notes.split("|") if line.strip()]

    lines = [
        "### Run",
        "",
        f"- Date: {date.today().isoformat()}",
        f"- Environment: local staging UI on `{frontend}` against `{api}`",
        f"- Tenant slug: `{tenant_slug}`",
        f"- User/account used: `{email}`",
        "- Auth path used: `local-session` self-serve signup",
        f"- Plan under test: `{plan}`",
        f"- Result: {result}",
        f"- First failed step: {first_failed_step}",
        "- Blockers found:",
    ]

    if blocker_lines:
        lines.extend([f"  - {line}" for line in blocker_lines])
    else:
        lines.append("  - none")

    lines.extend([
        "- Follow-up owner: Engineering",
        "- Notes:",
        f"  - manual non-chart UI pass executed across {route_note}",
    ])

    if note_lines:
        lines.extend([f"  - {line}" for line in note_lines])
    else:
        lines.append("  - result recorded without extra operator notes")

    lines.append("")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Append the latest manual non-chart UI result to the runtime export log.")
    parser.add_argument("--result", required=True, help="Result label, e.g. pass-with-chart-excluded or fail.")
    parser.add_argument("--first-failed-step", default="none")
    parser.add_argument("--blockers", default="none", help="Pipe-separated blocker list.")
    parser.add_argument("--notes", default="", help="Pipe-separated note list.")
    args = parser.parse_args()

    if not SESSION_PATH.exists():
        raise SystemExit(f"Session file not found: {SESSION_PATH}")
    RUNS_PATH.parent.mkdir(parents=True, exist_ok=True)

    session_text = SESSION_PATH.read_text(encoding="utf-8")
    entry = _build_entry(
        session_text=session_text,
        result=args.result,
        first_failed_step=args.first_failed_step,
        blockers=args.blockers,
        notes=args.notes,
    )

    existing = RUNS_PATH.read_text(encoding="utf-8").rstrip() + "\n\n" if RUNS_PATH.exists() else ""
    RUNS_PATH.write_text(existing + entry, encoding="utf-8")
    print(f"Appended non-chart UI result to {RUNS_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
