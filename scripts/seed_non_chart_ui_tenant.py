from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.request
from http.cookiejar import CookieJar
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def _parse_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip()
    return values


def _request_json(
    opener: urllib.request.OpenerDirector,
    url: str,
    *,
    method: str = "GET",
    payload: dict[str, object] | None = None,
) -> dict[str, object]:
    data = None
    headers = {}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    with opener.open(request, timeout=10) as response:
        raw = response.read().decode("utf-8")
        return json.loads(raw)


def _build_api_root(base_url: str) -> str:
    trimmed = base_url.rstrip("/")
    return trimmed[:-4] if trimmed.endswith("/api") else trimmed


def _render_session_markdown(payload: dict[str, object]) -> str:
    credentials = payload["credentials"]
    tenant = payload["tenant"]
    workspace = payload["workspace"]
    routes = payload["routes_to_check"]
    return "\n".join([
        "# Non-Chart UI Session",
        "",
        f"Date: {time.strftime('%Y-%m-%d')}",
        "",
        "Purpose: run the user-facing non-chart acceptance pass against live local staging without touching the chart rewrite.",
        "",
        "## Session Setup",
        "",
        f"- Frontend: `{payload['frontend_url']}`",
        f"- API: `{payload['api_base_url']}`",
        f"- Tenant slug: `{tenant['slug']}`",
        f"- Email: `{credentials['email']}`",
        f"- Name: `{credentials['name']}`",
        f"- Organization name: `{credentials['organization_name']}`",
        f"- Plan: `{tenant['plan_key']}`",
        f"- Expected workspace: `{workspace['expected_name']}`",
        f"- Expected onboarding progress: `{workspace['onboarding_progress_percent']}%`",
        "",
        "## Direct URLs",
        "",
        f"- Watchlist: `{routes[0]}`",
        f"- Workspaces: `{routes[1]}`",
        f"- Settings: `{routes[2]}`",
        f"- Release: `{routes[3]}`",
        "",
        "## Login Hint",
        "",
        payload["login_hint"],
        "",
        "## Validation Checklist",
        "",
        "- Shell truthfulness",
        "  - login succeeds",
        "  - tenant context remains in the URL",
        "  - non-production readiness banner appears",
        "  - no false healthy/demo state is shown during auth/bootstrap issues",
        "- Watchlist",
        "  - page renders",
        "  - rows load",
        "  - refresh works",
        "  - save workspace works",
        "- Workspaces",
        "  - `Personal Launchpad` appears",
        "  - saved watchlist workspace appears if created",
        "  - apply works",
        "  - pin/unpin works",
        "  - duplicate works",
        "- Settings",
        "  - org data loads",
        "  - billing summary loads",
        "  - entitlements load",
        "  - onboarding snapshot loads",
        "  - support snapshot loads",
        "- Release",
        "  - route loads",
        "  - metadata or empty state renders truthfully",
        "",
        "## Result Template",
        "",
        "- Result:",
        "- First failed step:",
        "- Blockers found:",
        "- Notes:",
        "",
        "## Follow-Up",
        "",
        "If this pass succeeds, append a `pass-with-chart-excluded` run to:",
        "",
        "- `runtime-exports/non_chart_ui_runs.md`",
        "",
        "If it fails, capture the exact route and visible error first, then patch only the non-chart surface involved.",
        "",
    ])


def main() -> int:
    parser = argparse.ArgumentParser(description="Seed a reusable non-chart UI staging tenant.")
    parser.add_argument("env_file", nargs="?", default=".env.staging", help="Path to the staging env file.")
    parser.add_argument("--session-file", default="runtime-exports/non_chart_ui_latest_session.md", help="Path to write the latest UI session markdown.")
    args = parser.parse_args()

    env_path = Path(args.env_file).resolve()
    if not env_path.exists():
        print(json.dumps({"status": "blocked", "message": f"env file not found: {env_path}"}, indent=2))
        return 1

    env = _parse_env_file(env_path)
    api_base_url = env.get("PUBLIC_API_BASE_URL", "").strip()
    if not api_base_url:
        print(json.dumps({"status": "blocked", "message": "PUBLIC_API_BASE_URL is missing."}, indent=2))
        return 1

    frontend_url = env.get("FRONTEND_DEV_URL", "http://localhost:5173").strip() or "http://localhost:5173"
    api_root = _build_api_root(api_base_url)
    stamp = time.strftime("%Y%m%d%H%M%S")
    email = f"staging.ui.nonchart.{stamp}@example.com"
    name = "Staging UI Validator"
    organization_name = f"Staging UI Org {stamp}"

    cookie_jar = CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cookie_jar))

    login_result = _request_json(
        opener,
        f"{api_root}/api/auth/login",
        method="POST",
        payload={
            "email": email,
            "name": name,
            "organization_name": organization_name,
            "create_organization_if_missing": True,
        },
    )
    _request_json(opener, f"{api_root}/api/orgs/onboarding/seed-workspace", method="POST")
    onboarding_result = _request_json(opener, f"{api_root}/api/orgs/onboarding")

    tenant = (login_result.get("data") or {}).get("active_tenant") or {}
    tenant_slug = tenant.get("slug")
    payload = {
        "status": "ready",
        "frontend_url": frontend_url,
        "api_base_url": api_base_url,
        "credentials": {
            "email": email,
            "name": name,
            "organization_name": organization_name,
        },
        "tenant": {
            "slug": tenant_slug,
            "name": tenant.get("name"),
            "plan_key": tenant.get("plan_key"),
        },
        "workspace": {
            "expected_name": "Personal Launchpad",
            "onboarding_progress_percent": (onboarding_result.get("data") or {}).get("progress_percent"),
            "workspace_count": (onboarding_result.get("data") or {}).get("workspace_count"),
        },
        "routes_to_check": [
            f"{frontend_url}/watchlist?tenant={tenant_slug}",
            f"{frontend_url}/workspaces?tenant={tenant_slug}",
            f"{frontend_url}/settings?tenant={tenant_slug}",
            f"{frontend_url}/release?tenant={tenant_slug}",
        ],
        "login_hint": "Use the email, display name, and organization name above on the local-session sign-in screen.",
        "session_file": args.session_file,
        "next_action": "Open the frontend and run the non-chart UI acceptance checklist with this seeded tenant.",
    }
    session_path = (PROJECT_ROOT / args.session_file).resolve()
    session_path.parent.mkdir(parents=True, exist_ok=True)
    session_path.write_text(_render_session_markdown(payload), encoding="utf-8")
    print(json.dumps(payload, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
