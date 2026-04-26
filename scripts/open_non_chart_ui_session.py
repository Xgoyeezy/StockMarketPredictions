from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SESSION_PATH = PROJECT_ROOT / "runtime-exports" / "non_chart_ui_latest_session.md"


def _extract_urls(text: str) -> list[str]:
    return re.findall(r"`(http://localhost:[0-9]+/[^`]+)`", text)


def main() -> int:
    if not SESSION_PATH.exists():
        raise SystemExit(f"Session file not found: {SESSION_PATH}")

    session_text = SESSION_PATH.read_text(encoding="utf-8")
    urls = _extract_urls(session_text)
    if not urls:
        raise SystemExit("No session URLs found in the latest non-chart UI session file.")

    opened: list[str] = []
    for url in urls:
        subprocess.Popen(["cmd", "/c", "start", "", url], cwd=PROJECT_ROOT)
        opened.append(url)

    subprocess.Popen(["cmd", "/c", "start", "", str(SESSION_PATH)], cwd=PROJECT_ROOT)
    print("Opened non-chart UI session URLs:")
    for url in opened:
        print(url)
    print(f"Opened session file: {SESSION_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
