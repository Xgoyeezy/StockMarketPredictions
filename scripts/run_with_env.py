from __future__ import annotations

import argparse
import os
import subprocess
from pathlib import Path


def _parse_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip()
    return values


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run a command with environment variables loaded from an env file.",
    )
    parser.add_argument("env_file", help="Path to the env file to load.")
    parser.add_argument("command", nargs=argparse.REMAINDER, help="Command to run after loading env values.")
    args = parser.parse_args()

    env_path = Path(args.env_file).resolve()
    if not env_path.exists():
        print(f"ERROR: env file not found: {env_path}")
        return 1

    command = list(args.command)
    if command and command[0] == "--":
        command = command[1:]
    if not command:
        print("ERROR: no command provided to run_with_env.py")
        return 1

    env = os.environ.copy()
    env.update(_parse_env_file(env_path))
    completed = subprocess.run(command, env=env)
    return int(completed.returncode)


if __name__ == "__main__":
    raise SystemExit(main())
