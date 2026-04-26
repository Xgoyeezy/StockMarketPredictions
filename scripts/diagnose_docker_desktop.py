from __future__ import annotations

from pathlib import Path


def _tail(path: Path, line_count: int = 40) -> list[str]:
    if not path.exists():
        return []
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    return lines[-line_count:]


def main() -> int:
    log_root = Path.home() / "AppData" / "Local" / "Docker" / "log" / "host"
    backend_log = log_root / "com.docker.backend.exe.log"
    desktop_stdout = log_root / "Docker Desktop.exe.stdout.log"
    desktop_stderr = log_root / "Docker Desktop.exe.stderr.log"

    print("DOCKER_DESKTOP_DIAGNOSTIC")
    print(f"- log_root: {log_root}")
    print(f"- backend_log_exists: {backend_log.exists()}")
    print(f"- desktop_stdout_exists: {desktop_stdout.exists()}")
    print(f"- desktop_stderr_exists: {desktop_stderr.exists()}")

    backend_tail = _tail(backend_log)
    timeout_lines = [line for line in backend_tail if "context deadline exceeded" in line or "returning engine error" in line]

    print("RECENT_ENGINE_SIGNALS:")
    if timeout_lines:
        for line in timeout_lines[-10:]:
            print(f"- {line}")
    else:
        print("- No obvious engine timeout lines found in the recent backend log tail.")

    stderr_tail = _tail(desktop_stderr, line_count=20)
    if stderr_tail:
        print("DESKTOP_STDERR_TAIL:")
        for line in stderr_tail:
            print(f"- {line}")

    print("NEXT_ACTION:")
    if timeout_lines:
        print("- Docker Desktop UI is launching, but the engine is still unhealthy. Restart Docker Desktop or switch to a managed Postgres URL for staging.")
        return 1

    print("- No recent engine timeout signature found. Re-run the local staging preflight.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
