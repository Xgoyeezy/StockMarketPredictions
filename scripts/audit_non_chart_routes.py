from __future__ import annotations

import json
import re
import sys
from collections import deque
from pathlib import Path


IMPORT_RE = re.compile(
    r"""(?:import\s+(?:[^'"]+?\s+from\s+)?|import\s*\()\s*['"](?P<path>[^'"]+)['"]""",
    re.MULTILINE,
)

SAFE_ROUTE_FILES = {
    "watchlist": "frontend/src/pages/WatchlistPage.jsx",
    "workspaces": "frontend/src/pages/WorkspacesPage.jsx",
    "settings": "frontend/src/pages/SettingsPage.jsx",
    "release": "frontend/src/pages/ReleasePage.jsx",
}

CHART_ENTRY_FILES = {
    "frontend/src/components/CustomMarketChart.jsx",
}

CHART_MARKERS = (
    "/chart-engine/",
    "\\chart-engine\\",
    "CustomMarketChart",
)

EXTENSIONS = ("", ".js", ".jsx", ".ts", ".tsx")


def project_root() -> Path:
    return Path(__file__).resolve().parent.parent


def normalize(path: Path) -> str:
    return path.resolve().as_posix()


def resolve_import(source_file: Path, import_path: str) -> Path | None:
    if not import_path.startswith("."):
        return None

    base = (source_file.parent / import_path).resolve()
    candidates = []

    for suffix in EXTENSIONS:
        candidates.append(Path(f"{base}{suffix}"))

    if base.is_dir():
        for extension in (".js", ".jsx", ".ts", ".tsx"):
            candidates.append(base / f"index{extension}")

    for candidate in candidates:
        if candidate.exists() and candidate.is_file():
            return candidate

    return None


def read_imports(source_file: Path) -> list[Path]:
    try:
        content = source_file.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        content = source_file.read_text(encoding="utf-8", errors="ignore")

    imports: list[Path] = []
    for match in IMPORT_RE.finditer(content):
        resolved = resolve_import(source_file, match.group("path"))
        if resolved is not None:
            imports.append(resolved)
    return imports


def is_chart_related(path: Path, chart_entries: set[str]) -> bool:
    normalized = normalize(path)
    if normalized in chart_entries:
        return True
    return any(marker in normalized for marker in CHART_MARKERS)


def audit_route(entry_file: Path, chart_entries: set[str]) -> dict[str, object]:
    visited: set[str] = set()
    chart_paths: set[str] = set()
    queue = deque([entry_file])

    while queue:
        current = queue.popleft()
        current_key = normalize(current)
        if current_key in visited:
            continue
        visited.add(current_key)

        if is_chart_related(current, chart_entries):
            chart_paths.add(current_key)

        for imported in read_imports(current):
            imported_key = normalize(imported)
            if is_chart_related(imported, chart_entries):
                chart_paths.add(imported_key)
            if imported_key not in visited:
                queue.append(imported)

    return {
        "entry_file": normalize(entry_file),
        "file_count": len(visited),
        "chart_dependency_found": bool(chart_paths),
        "chart_related_files": sorted(chart_paths),
    }


def main() -> int:
    root = project_root()
    chart_entries = {normalize(root / relative_path) for relative_path in CHART_ENTRY_FILES}
    routes: dict[str, object] = {}
    blockers: list[str] = []

    for route_name, relative_path in SAFE_ROUTE_FILES.items():
        entry = root / relative_path
        if not entry.exists():
            blockers.append(f"Missing route entry: {relative_path}")
            continue

        result = audit_route(entry, chart_entries)
        routes[route_name] = result
        if result["chart_dependency_found"]:
            blockers.append(f"Route '{route_name}' depends on chart-related code.")

    status = "ready" if not blockers else "blocked"
    payload = {
        "status": status,
        "safe_routes_checked": sorted(SAFE_ROUTE_FILES),
        "routes": routes,
        "blockers": blockers,
        "next_action": (
            "Non-chart route isolation is intact. Run the UI checklist against staging next."
            if status == "ready"
            else "Remove chart dependencies from the blocked safe routes before using them for pilot validation."
        ),
    }
    print(json.dumps(payload, indent=2))
    return 0 if status == "ready" else 1


if __name__ == "__main__":
    sys.exit(main())
