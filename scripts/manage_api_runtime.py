from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def project_root() -> Path:
    return PROJECT_ROOT


def parse_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip()
    return values


def bool_from_env(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def resolve_env_file(env_file: str) -> Path:
    path = Path(env_file)
    if not path.is_absolute():
        path = project_root() / path
    return path.resolve()


def runtime_name_for_env_file(env_path: Path) -> str:
    normalized = env_path.name.strip().lower()
    if normalized == ".env":
        return "local-api"
    if normalized == ".env.staging":
        return "staging-api"
    safe_name = "".join(ch if ch.isalnum() else "-" for ch in normalized).strip("-") or "runtime"
    while "--" in safe_name:
        safe_name = safe_name.replace("--", "-")
    return f"{safe_name}-api"


def normalize_runtime_probe_host(host: str | None) -> str:
    normalized = str(host or "").strip()
    if normalized in {"", "0.0.0.0", "::", "[::]", "localhost"}:
        return "127.0.0.1"
    return normalized


def resolve_python_path(root: Path) -> Path:
    candidates = [
        root / "backend" / ".venv" / "Scripts" / "python.exe",
        root / "backend" / ".venv" / "bin" / "python",
        Path(sys.executable),
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate.resolve()
    raise FileNotFoundError("Could not resolve a Python executable for backend runtime control.")


def runtime_artifact_paths(root: Path, runtime_name: str) -> dict[str, Path]:
    logs_dir = root / ".codex-logs" / "api-runtime"
    logs_dir.mkdir(parents=True, exist_ok=True)
    return {
        "logs_dir": logs_dir,
        "state_path": logs_dir / f"{runtime_name}.json",
        "stdout_path": logs_dir / f"{runtime_name}.out.log",
        "stderr_path": logs_dir / f"{runtime_name}.err.log",
    }


def _ps_quote(value: str) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def _cmd_quote(value: str | Path) -> str:
    return '"' + str(value).replace('"', '""') + '"'


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def safe_json_loads(raw: str) -> Any:
    try:
        return json.loads(raw)
    except Exception:
        return None


def probe_url(url: str, *, timeout_seconds: float = 2.0) -> dict[str, Any]:
    request = Request(url, method="GET")
    try:
        with urlopen(request, timeout=timeout_seconds) as response:
            raw = response.read().decode("utf-8", errors="replace")
            payload = safe_json_loads(raw)
            return {
                "reachable": True,
                "status_code": int(getattr(response, "status", 0) or 0),
                "url": url,
                "payload": payload,
                "body": raw if payload is None else None,
                "error": None,
            }
    except HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        payload = safe_json_loads(raw)
        return {
            "reachable": True,
            "status_code": int(exc.code),
            "url": url,
            "payload": payload,
            "body": raw if payload is None else None,
            "error": None,
        }
    except URLError as exc:
        return {
            "reachable": False,
            "status_code": None,
            "url": url,
            "payload": None,
            "body": None,
            "error": str(exc.reason or exc),
        }
    except Exception as exc:  # pragma: no cover - defensive guard
        return {
            "reachable": False,
            "status_code": None,
            "url": url,
            "payload": None,
            "body": None,
            "error": str(exc),
        }


def _parse_windows_netstat_pids(raw_output: str, port: int) -> list[int]:
    matches: list[int] = []
    needle = str(int(port))
    for raw_line in raw_output.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        parts = line.split()
        if len(parts) < 5 or parts[0].upper() != "TCP":
            continue
        local_address = parts[1]
        state = parts[3].upper()
        pid_raw = parts[4]
        try:
            local_port = local_address.rsplit(":", 1)[-1]
        except Exception:
            continue
        if local_port != needle or state != "LISTENING":
            continue
        try:
            pid = int(pid_raw)
        except ValueError:
            continue
        if pid not in matches:
            matches.append(pid)
    return matches


def listener_pids_for_port(port: int) -> list[int]:
    if os.name == "nt":
        try:
            completed = subprocess.run(
                ["cmd", "/c", "netstat -ano -p tcp"],
                capture_output=True,
                text=True,
                check=False,
                timeout=10,
            )
        except Exception:
            return []
        return _parse_windows_netstat_pids(completed.stdout or "", port)
    commands = [
        ["lsof", "-nP", f"-iTCP:{port}", "-sTCP:LISTEN", "-t"],
        ["ss", "-ltnp"],
    ]
    for command in commands:
        try:
            completed = subprocess.run(command, capture_output=True, text=True, check=False, timeout=10)
        except Exception:
            continue
        stdout = completed.stdout or ""
        if not stdout.strip():
            continue
        if command[0] == "lsof":
            matches = []
            for line in stdout.splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    matches.append(int(line))
                except ValueError:
                    continue
            return matches
        matches = []
        for line in stdout.splitlines():
            if f":{int(port)}" not in line or "LISTEN" not in line.upper():
                continue
            if "pid=" not in line:
                continue
            segment = line.split("pid=", 1)[1]
            pid_raw = "".join(ch for ch in segment if ch.isdigit())
            if not pid_raw:
                continue
            pid = int(pid_raw)
            if pid not in matches:
                matches.append(pid)
        return matches
    return []


def process_exists(pid: int | None) -> bool:
    if not pid or pid <= 0:
        return False
    if os.name == "nt":
        try:
            completed = subprocess.run(
                ["cmd", "/c", "tasklist", "/FI", f"PID eq {int(pid)}"],
                capture_output=True,
                text=True,
                check=False,
                timeout=10,
            )
        except Exception:
            return False
        stdout = (completed.stdout or "").lower()
        return f" {int(pid)} " in f" {stdout} " and "no tasks are running" not in stdout
    try:
        os.kill(int(pid), 0)
    except OSError:
        return False
    return True


def terminate_process(pid: int) -> bool:
    if not process_exists(pid):
        return False
    if os.name == "nt":
        completed = subprocess.run(
            ["cmd", "/c", "taskkill", "/PID", str(int(pid)), "/T", "/F"],
            capture_output=True,
            text=True,
            check=False,
            timeout=20,
        )
        return completed.returncode == 0
    try:
        os.kill(int(pid), 15)
        return True
    except OSError:
        return False


def load_runtime_state(state_path: Path) -> dict[str, Any] | None:
    if not state_path.exists():
        return None
    try:
        return json.loads(state_path.read_text(encoding="utf-8"))
    except Exception:
        return None


def write_runtime_state(state_path: Path, payload: dict[str, Any]) -> None:
    state_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def build_runtime_config(env_file: str) -> dict[str, Any]:
    root = project_root()
    env_path = resolve_env_file(env_file)
    env_values = parse_env_file(env_path)
    runtime_name = runtime_name_for_env_file(env_path)
    artifacts = runtime_artifact_paths(root, runtime_name)
    merged_env = os.environ.copy()
    merged_env.update(env_values)
    port = int(merged_env.get("API_PORT", "8000"))
    api_prefix = str(merged_env.get("API_PREFIX", "/api") or "/api").rstrip("/")
    api_base_url = str(merged_env.get("RUNTIME_API_BASE_URL", "") or "").rstrip("/")
    if not api_base_url:
        api_host = normalize_runtime_probe_host(merged_env.get("API_HOST"))
        api_base_url = f"http://{api_host}:{port}{api_prefix}"
    return {
        "root": root,
        "env_path": env_path,
        "env_values": env_values,
        "runtime_name": runtime_name,
        "port": port,
        "api_base_url": api_base_url,
        "health_url": f"{api_base_url}/healthz",
        "ready_url": f"{api_base_url}/readyz",
        "python_path": resolve_python_path(root),
        **artifacts,
    }


def collect_runtime_probe(config: dict[str, Any], *, state: dict[str, Any] | None = None) -> dict[str, Any]:
    health = probe_url(config["health_url"])
    ready = probe_url(config["ready_url"], timeout_seconds=10.0)
    listener_pids = listener_pids_for_port(int(config["port"]))
    listener_pid = listener_pids[0] if listener_pids else None
    managed_pid = None
    if isinstance(state, dict):
        managed_pid = state.get("spawn_pid") or state.get("pid")
    state_exists = state is not None
    if health.get("status_code") == 200:
        status = "ready" if ready.get("status_code") == 200 else "warning"
    elif listener_pid:
        status = "blocked"
    elif managed_pid and process_exists(int(managed_pid)):
        status = "warning"
    else:
        status = "stopped"
    next_action = (
        "Backend runtime is healthy."
        if status == "ready"
        else "Inspect readiness warnings."
        if status == "warning"
        else "Stop the conflicting listener or change the configured API port."
        if status == "blocked"
        else f"Start the backend with {config['python_path']} scripts/manage_api_runtime.py start --env-file {config['env_path'].name}"
    )
    return {
        "status": status,
        "runtime_name": config["runtime_name"],
        "env_file": str(config["env_path"]),
        "api_base_url": config["api_base_url"],
        "health_url": config["health_url"],
        "ready_url": config["ready_url"],
        "port": int(config["port"]),
        "listener_pid": listener_pid,
        "listener_pids": listener_pids,
        "managed_pid": managed_pid,
        "managed_pid_running": process_exists(int(managed_pid)) if managed_pid else False,
        "state_file": str(config["state_path"]),
        "state_file_exists": state_exists,
        "stdout_log": str(config["stdout_path"]),
        "stderr_log": str(config["stderr_path"]),
        "health": health,
        "ready": ready,
        "checked_at": utc_now_iso(),
        "next_action": next_action,
    }


def _wait_for_startup(config: dict[str, Any], *, spawn_pid: int | None, timeout_seconds: float) -> dict[str, Any]:
    deadline = time.time() + timeout_seconds
    last_probe = collect_runtime_probe(config, state={"spawn_pid": spawn_pid})
    while time.time() < deadline:
        last_probe = collect_runtime_probe(config, state={"spawn_pid": spawn_pid})
        if last_probe["health"].get("status_code") == 200:
            return last_probe
        if spawn_pid and not process_exists(spawn_pid) and not last_probe.get("listener_pid"):
            break
        time.sleep(0.5)
    return last_probe


def _base_creation_flags() -> int:
    if os.name != "nt":
        return 0
    flags = 0
    for name in ("CREATE_NEW_PROCESS_GROUP", "DETACHED_PROCESS", "CREATE_BREAKAWAY_FROM_JOB"):
        flags |= int(getattr(subprocess, name, 0))
    return flags


def _start_detached_windows(config: dict[str, Any]) -> int:
    runner_path = config["root"] / "scripts" / "run_with_env.py"
    backend_command = " ".join(
        [
            _cmd_quote(config["python_path"]),
            _cmd_quote(runner_path),
            _cmd_quote(config["env_path"]),
            "--",
            _cmd_quote(config["python_path"]),
            "-m",
            "backend.app",
            f"1> {_cmd_quote(config['stdout_path'])}",
            f"2> {_cmd_quote(config['stderr_path'])}",
        ]
    )
    argument_list = "@(" + ", ".join(
        _ps_quote(argument)
        for argument in [
            "/d",
            "/c",
            f'"{backend_command}"',
        ]
    ) + ")"
    script = "\n".join(
        [
            "$ErrorActionPreference = 'Stop'",
            "$commandProcessor = $env:ComSpec",
            "$process = Start-Process "
            "-FilePath $commandProcessor "
            f"-ArgumentList {argument_list} "
            f"-WorkingDirectory {_ps_quote(str(config['root']))} "
            "-WindowStyle Hidden "
            "-PassThru",
            "$process.Id",
        ]
    )
    completed = subprocess.run(
        [
            "powershell",
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-Command",
            script,
        ],
        cwd=config["root"],
        capture_output=True,
        text=True,
        check=False,
        timeout=20,
    )
    if completed.returncode != 0:
        details = (completed.stderr or completed.stdout or "").strip()
        raise RuntimeError(f"Failed to start backend runtime with PowerShell Start-Process: {details}")
    for line in reversed((completed.stdout or "").splitlines()):
        line = line.strip()
        if not line:
            continue
        try:
            return int(line)
        except ValueError:
            continue
    raise RuntimeError("PowerShell Start-Process did not return a backend runtime PID.")


def start_runtime(env_file: str, *, timeout_seconds: float = 60.0) -> tuple[dict[str, Any], int]:
    config = build_runtime_config(env_file)
    state = load_runtime_state(config["state_path"])
    current_probe = collect_runtime_probe(config, state=state)
    if current_probe["status"] in {"ready", "warning"}:
        payload = {
            **current_probe,
            "start_result": "already_running",
        }
        write_runtime_state(config["state_path"], payload)
        return payload, 0
    if current_probe["listener_pid"]:
        payload = {
            **current_probe,
            "start_result": "port_conflict",
        }
        write_runtime_state(config["state_path"], payload)
        return payload, 1

    if os.name == "nt":
        spawn_pid = _start_detached_windows(config)
    else:
        stdout_handle = config["stdout_path"].open("ab")
        stderr_handle = config["stderr_path"].open("ab")
        env = os.environ.copy()
        env.update(config["env_values"])
        env.setdefault("API_RELOAD", "false")
        try:
            process = subprocess.Popen(
                [str(config["python_path"]), "-m", "backend.app"],
                cwd=config["root"],
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=stdout_handle,
                stderr=stderr_handle,
                creationflags=_base_creation_flags(),
                start_new_session=True,
                close_fds=True,
            )
            spawn_pid = int(process.pid)
        finally:
            stdout_handle.close()
            stderr_handle.close()

    probe = _wait_for_startup(config, spawn_pid=spawn_pid, timeout_seconds=timeout_seconds)
    effective_spawn_pid = spawn_pid or probe.get("listener_pid")
    payload = {
        **probe,
        "start_result": "started",
        "spawn_pid": effective_spawn_pid,
        "started_at": utc_now_iso(),
    }
    write_runtime_state(config["state_path"], payload)
    return payload, 0 if payload["status"] in {"ready", "warning"} else 1


def runtime_status(env_file: str) -> tuple[dict[str, Any], int]:
    config = build_runtime_config(env_file)
    state = load_runtime_state(config["state_path"])
    payload = collect_runtime_probe(config, state=state)
    if state:
        payload["started_at"] = state.get("started_at")
        payload["start_result"] = state.get("start_result")
        payload["spawn_pid"] = state.get("spawn_pid")
    if state or payload["listener_pid"]:
        write_runtime_state(config["state_path"], payload)
    return payload, 0 if payload["status"] in {"ready", "warning"} else 1


def stop_runtime(env_file: str, *, timeout_seconds: float = 15.0) -> tuple[dict[str, Any], int]:
    config = build_runtime_config(env_file)
    state = load_runtime_state(config["state_path"])
    existing_probe = collect_runtime_probe(config, state=state)
    target_pids: list[int] = []
    for candidate in (
        existing_probe.get("listener_pid"),
        existing_probe.get("managed_pid"),
        (state or {}).get("spawn_pid") if state else None,
        (state or {}).get("listener_pid") if state else None,
    ):
        if candidate is None:
            continue
        try:
            pid = int(candidate)
        except (TypeError, ValueError):
            continue
        if pid > 0 and pid not in target_pids:
            target_pids.append(pid)
    stopped_pids: list[int] = []
    for pid in target_pids:
        if terminate_process(pid):
            stopped_pids.append(pid)

    deadline = time.time() + timeout_seconds
    final_probe = collect_runtime_probe(config, state=state)
    while time.time() < deadline and (final_probe["listener_pid"] or final_probe["managed_pid_running"]):
        time.sleep(0.5)
        final_probe = collect_runtime_probe(config, state=state)

    if config["state_path"].exists():
        try:
            config["state_path"].unlink()
        except OSError:
            pass

    payload = {
        **final_probe,
        "status": "stopped" if not final_probe["listener_pid"] and not final_probe["managed_pid_running"] else "warning",
        "stopped_pids": stopped_pids,
        "stopped_at": utc_now_iso(),
        "next_action": (
            f"Start the backend with {config['python_path']} scripts/manage_api_runtime.py start --env-file {config['env_path'].name}"
            if not final_probe["listener_pid"]
            else "A listener is still bound to the API port. Inspect the remaining process before retrying."
        ),
    }
    return payload, 0 if payload["status"] == "stopped" else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Start, inspect, or stop the FastAPI backend in a detached local runtime.")
    subparsers = parser.add_subparsers(dest="command", required=True)
    for command in ("start", "status", "stop"):
        subparser = subparsers.add_parser(command)
        subparser.add_argument("--env-file", default=".env", help="Env file to load before starting or probing the backend.")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "start":
        payload, exit_code = start_runtime(args.env_file)
    elif args.command == "status":
        payload, exit_code = runtime_status(args.env_file)
    else:
        payload, exit_code = stop_runtime(args.env_file)
    print(json.dumps(payload, indent=2))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
