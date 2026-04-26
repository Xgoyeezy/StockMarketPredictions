from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.parse
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


def main() -> int:
    parser = argparse.ArgumentParser(description="Run a backend-side staging personal acceptance smoke test.")
    parser.add_argument("env_file", nargs="?", default=".env.staging", help="Path to the staging env file.")
    args = parser.parse_args()

    env_path = Path(args.env_file).resolve()
    if not env_path.exists():
        print(json.dumps({"status": "blocked", "message": f"env file not found: {env_path}"}, indent=2))
        return 1

    env = _parse_env_file(env_path)
    base_url = env.get("PUBLIC_API_BASE_URL", "").strip()
    if not base_url:
        print(json.dumps({"status": "blocked", "message": "PUBLIC_API_BASE_URL is missing."}, indent=2))
        return 1

    api_root = _build_api_root(base_url)
    stamp = time.strftime("%Y%m%d%H%M%S")
    email = f"staging.acceptance.smoke.{stamp}@example.com"
    organization_name = f"Staging Smoke Org {stamp}"
    login_payload = {
        "email": email,
        "name": "Staging Acceptance",
        "organization_name": organization_name,
        "create_organization_if_missing": True,
    }

    cookie_jar = CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cookie_jar))
    report: dict[str, object] = {
        "status": "blocked",
        "base_url": base_url,
        "email": email,
        "organization_name": organization_name,
        "checks": [],
    }

    def step(name: str, fn):
        try:
            result = fn()
            report["checks"].append({"key": name, "status": "ready", "result": result})
            return result
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            report["checks"].append({"key": name, "status": "blocked", "error": f"HTTP {exc.code}", "body": body})
            raise
        except Exception as exc:  # pragma: no cover - defensive
            report["checks"].append({"key": name, "status": "blocked", "error": str(exc)})
            raise

    try:
        step("auth_config", lambda: _request_json(opener, f"{api_root}/api/auth/config"))
        step("auth_entry", lambda: _request_json(opener, f"{api_root}/api/auth/entry"))
        login_result = step("auth_login", lambda: _request_json(opener, f"{api_root}/api/auth/login", method="POST", payload=login_payload))
        session_result = step("auth_session", lambda: _request_json(opener, f"{api_root}/api/auth/session"))
        orgs_result = step("orgs", lambda: _request_json(opener, f"{api_root}/api/orgs"))
        summary_result = step("billing_summary", lambda: _request_json(opener, f"{api_root}/api/billing/summary"))
        entitlements_result = step("billing_entitlements", lambda: _request_json(opener, f"{api_root}/api/billing/entitlements"))
        support_result = step("org_support", lambda: _request_json(opener, f"{api_root}/api/orgs/support"))
        onboarding_before = step("onboarding_before", lambda: _request_json(opener, f"{api_root}/api/orgs/onboarding"))
        seed_result = step("seed_workspace", lambda: _request_json(opener, f"{api_root}/api/orgs/onboarding/seed-workspace", method="POST"))
        onboarding_after = step("onboarding_after", lambda: _request_json(opener, f"{api_root}/api/orgs/onboarding"))

        report["status"] = "ready"
        report["summary"] = {
            "tenant_slug": (((login_result.get("data") or {}).get("active_tenant") or {}).get("slug")),
            "plan_key": (((summary_result.get("data") or {}).get("plan") or {}).get("key")),
            "authenticated": bool((session_result.get("data") or {}).get("authenticated")),
            "workspace_count_before": ((onboarding_before.get("data") or {}).get("workspace_count")),
            "workspace_count_after": ((onboarding_after.get("data") or {}).get("workspace_count")),
            "seeded_workspace_name": (((seed_result.get("data") or {}).get("workspace") or {}).get("name")),
            "support_snapshot_loaded": bool((support_result.get("ok") is True)),
            "entitlements_loaded": bool((entitlements_result.get("ok") is True)),
            "org_count": ((orgs_result.get("data") or {}).get("count")),
        }
        report["next_action"] = "Backend-side staging personal smoke passed. Validate the user-facing first-value UI path next."
        print(json.dumps(report, indent=2))
        return 0
    except Exception:
        report["next_action"] = "Fix the first blocked acceptance step before relying on the personal-use path."
        print(json.dumps(report, indent=2))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
