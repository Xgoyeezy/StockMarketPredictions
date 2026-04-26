from __future__ import annotations

from scripts.manage_api_runtime import main as runtime_main


if __name__ == "__main__":
    raise SystemExit(runtime_main(["start", "--env-file", ".env.staging"]))
