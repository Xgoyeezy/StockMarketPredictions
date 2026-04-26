from __future__ import annotations

import argparse
from pathlib import Path


DEFAULT_ENV_FILE = Path("frontend/.env.local")


def upsert(lines: list[str], key: str, value: str) -> list[str]:
    prefix = f"{key}="
    replaced = False
    updated: list[str] = []

    for line in lines:
        if line.startswith(prefix):
            updated.append(f"{prefix}{value}")
            replaced = True
        else:
            updated.append(line)

    if not replaced:
        updated.append(f"{prefix}{value}")

    return updated


def main() -> int:
    parser = argparse.ArgumentParser(description="Set frontend local API and WS targets.")
    parser.add_argument("--api-base-url", required=True)
    parser.add_argument("--ws-base-url")
    parser.add_argument("env_file", nargs="?", default=str(DEFAULT_ENV_FILE))
    args = parser.parse_args()

    env_path = Path(args.env_file)
    env_path.parent.mkdir(parents=True, exist_ok=True)
    lines = env_path.read_text(encoding="utf-8").splitlines() if env_path.exists() else []

    ws_base_url = args.ws_base_url
    if not ws_base_url:
        if args.api_base_url.startswith("https://"):
            ws_base_url = "wss://" + args.api_base_url[len("https://"):]
        elif args.api_base_url.startswith("http://"):
            ws_base_url = "ws://" + args.api_base_url[len("http://"):]
        else:
            ws_base_url = args.api_base_url

    lines = upsert(lines, "VITE_API_BASE_URL", args.api_base_url)
    lines = upsert(lines, "VITE_WS_BASE_URL", ws_base_url)
    env_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Updated {env_path} with frontend API target {args.api_base_url}")
    print(f"Updated {env_path} with frontend WS target {ws_base_url}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
