from __future__ import annotations

import argparse
import ctypes
import json
import os
import signal
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = REPO_ROOT / "institutional_trading" / "config" / "example.yaml"
DEFAULT_LOG_DIR = REPO_ROOT / ".codex-logs" / "institutional-trading"
MANAGER_STATE = "manager.json"
STDOUT_LOG = "service.stdout.log"
STDERR_LOG = "service.stderr.log"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="manage_institutional_trading")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG))
    parser.add_argument("--runtime-dir", default=None)
    parser.add_argument("--log-dir", default=str(DEFAULT_LOG_DIR))
    parser.add_argument("--heartbeat-interval", type=float, default=1.0)
    parser.add_argument("--timeout", type=float, default=10.0)
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("start", "stop", "status", "health", "logs", "reconcile"):
        sub.add_parser(name)
    kill = sub.add_parser("kill")
    kill.add_argument("--reason", required=True)
    replay = sub.add_parser("replay")
    replay.add_argument("--audit-log", default=None)
    args = parser.parse_args(argv)

    manager = ProcessManager(
        config_path=Path(args.config),
        runtime_dir=Path(args.runtime_dir) if args.runtime_dir else None,
        log_dir=Path(args.log_dir),
        heartbeat_interval=args.heartbeat_interval,
        timeout=args.timeout,
    )
    if args.command == "start":
        return _print(manager.start())
    if args.command == "stop":
        return _print(manager.stop())
    if args.command == "status":
        return _print(manager.status())
    if args.command == "health":
        return _print(manager.invoke_cli("health"))
    if args.command == "kill":
        return _print(manager.invoke_cli("kill", "--reason", args.reason))
    if args.command == "reconcile":
        return _print(manager.invoke_cli("reconcile"))
    if args.command == "replay":
        audit_log = args.audit_log or str(manager.default_audit_log())
        return _print(manager.invoke_cli("replay", "--audit-log", audit_log))
    if args.command == "logs":
        return _print(manager.logs())
    raise AssertionError(args.command)


class ProcessManager:
    def __init__(self, *, config_path: Path, runtime_dir: Path | None, log_dir: Path, heartbeat_interval: float, timeout: float) -> None:
        self.config_path = config_path
        self.runtime_dir = runtime_dir
        self.log_dir = log_dir
        self.heartbeat_interval = max(float(heartbeat_interval), 0.05)
        self.timeout = max(float(timeout), 1.0)

    def start(self) -> dict[str, Any]:
        self.log_dir.mkdir(parents=True, exist_ok=True)
        state = self._read_state()
        pid = int(state.get("pid") or 0)
        if pid and process_running(pid):
            return {"status": "already_running", "pid": pid, "service": self.status()}
        stdout_path = self.log_dir / STDOUT_LOG
        stderr_path = self.log_dir / STDERR_LOG
        command = [
            python_executable(),
            "-m",
            "institutional_trading.cli",
            "--config",
            str(self.config_path),
        ]
        if self.runtime_dir:
            command.extend(["--runtime-dir", str(self.runtime_dir)])
        command.extend(["run", "--heartbeat-interval", str(self.heartbeat_interval)])
        env = os.environ.copy()
        env["PYTHONPATH"] = prepend_path(str(REPO_ROOT), env.get("PYTHONPATH"))
        creationflags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
        stdout = stdout_path.open("ab")
        stderr = stderr_path.open("ab")
        try:
            process = subprocess.Popen(command, cwd=REPO_ROOT, env=env, stdout=stdout, stderr=stderr, creationflags=creationflags)
        finally:
            stdout.close()
            stderr.close()
        state = {"pid": process.pid, "command": command, "started_at": utc_iso(), "config_path": str(self.config_path), "runtime_dir": str(self.runtime_dir) if self.runtime_dir else None, "stdout_log": str(stdout_path), "stderr_log": str(stderr_path)}
        self._write_state(state)
        service_status = self._wait_for_status(expected_running=True)
        if not process_running(process.pid):
            return {"status": "failed", "pid": process.pid, "service": service_status, "stderr_log": tail_file(stderr_path)}
        return {"status": "started", "pid": process.pid, "service": service_status, "stdout_log": str(stdout_path), "stderr_log": str(stderr_path)}

    def stop(self) -> dict[str, Any]:
        state = self._read_state()
        pid = int(state.get("pid") or 0)
        if pid and process_running(pid):
            request_stop(pid)
            deadline = time.monotonic() + self.timeout
            while time.monotonic() < deadline and process_running(pid):
                time.sleep(0.1)
            if process_running(pid):
                force_stop(pid)
        service = self.invoke_cli("stop")
        status = self._wait_for_status(expected_running=False)
        return {"status": "stopped", "pid": pid or None, "process_running": process_running(pid) if pid else False, "service": status or service}

    def status(self) -> dict[str, Any]:
        state = self._read_state()
        pid = int(state.get("pid") or 0)
        service = self.invoke_cli("status")
        return {"manager": state, "pid": pid or None, "process_running": process_running(pid) if pid else False, "service": service}

    def logs(self) -> dict[str, Any]:
        return {"stdout_log": str(self.log_dir / STDOUT_LOG), "stdout_tail": tail_file(self.log_dir / STDOUT_LOG), "stderr_log": str(self.log_dir / STDERR_LOG), "stderr_tail": tail_file(self.log_dir / STDERR_LOG)}

    def invoke_cli(self, *cli_args: str) -> dict[str, Any]:
        command = [python_executable(), "-m", "institutional_trading.cli", "--config", str(self.config_path)]
        if self.runtime_dir:
            command.extend(["--runtime-dir", str(self.runtime_dir)])
        command.extend(cli_args)
        env = os.environ.copy()
        env["PYTHONPATH"] = prepend_path(str(REPO_ROOT), env.get("PYTHONPATH"))
        result = subprocess.run(command, cwd=REPO_ROOT, env=env, text=True, capture_output=True, timeout=self.timeout)
        payload = _loads_json(result.stdout) if result.stdout.strip() else {}
        if result.returncode != 0:
            return {"status": "error", "returncode": result.returncode, "stdout": result.stdout, "stderr": result.stderr, "payload": payload}
        return payload

    def default_audit_log(self) -> Path:
        if self.runtime_dir:
            return self.runtime_dir / "audit" / "events.jsonl"
        return REPO_ROOT / "runtime" / "institutional_trading" / "audit" / "events.jsonl"

    def _wait_for_status(self, *, expected_running: bool) -> dict[str, Any]:
        deadline = time.monotonic() + self.timeout
        last: dict[str, Any] = {}
        while time.monotonic() < deadline:
            last = self.invoke_cli("status")
            service = last.get("service") if "service" in last else last
            if service.get("running") is expected_running:
                return service
            time.sleep(0.1)
        return last

    def _state_path(self) -> Path:
        return self.log_dir / MANAGER_STATE

    def _read_state(self) -> dict[str, Any]:
        path = self._state_path()
        if not path.exists():
            return {}
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {"state_error": "manager_state_unreadable", "path": str(path)}

    def _write_state(self, state: dict[str, Any]) -> None:
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self._state_path().write_text(json.dumps(state, indent=2, sort_keys=True), encoding="utf-8")


def python_executable() -> str:
    configured = os.environ.get("INSTITUTIONAL_TRADING_PYTHON")
    if configured:
        return configured
    backend_python = REPO_ROOT / "backend" / ".venv" / "Scripts" / "python.exe"
    if backend_python.exists():
        return str(backend_python)
    return sys.executable


def process_running(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        return windows_process_running(pid)
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def windows_process_running(pid: int) -> bool:
    kernel32 = ctypes.windll.kernel32
    handle = kernel32.OpenProcess(0x1000, False, int(pid))
    if not handle:
        return False
    try:
        exit_code = ctypes.c_ulong()
        if not kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
            return False
        return exit_code.value == 259
    finally:
        kernel32.CloseHandle(handle)


def request_stop(pid: int) -> None:
    if os.name == "nt" and hasattr(signal, "CTRL_BREAK_EVENT"):
        try:
            os.kill(pid, signal.CTRL_BREAK_EVENT)
            return
        except OSError:
            pass
    try:
        os.kill(pid, signal.SIGTERM)
    except OSError:
        pass


def force_stop(pid: int) -> None:
    if os.name == "nt":
        subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True, text=True)
        return
    try:
        os.kill(pid, signal.SIGKILL)
    except OSError:
        pass


def prepend_path(value: str, current: str | None) -> str:
    if not current:
        return value
    parts = current.split(os.pathsep)
    if value in parts:
        return current
    return value + os.pathsep + current


def tail_file(path: Path, limit: int = 4000) -> str:
    if not path.exists():
        return ""
    data = path.read_bytes()
    return data[-limit:].decode("utf-8", errors="replace")


def utc_iso() -> str:
    return datetime.now(tz=UTC).isoformat()


def _loads_json(value: str) -> dict[str, Any]:
    try:
        loaded = json.loads(value)
    except json.JSONDecodeError:
        return {"raw": value}
    return loaded if isinstance(loaded, dict) else {"value": loaded}


def _print(payload: dict[str, Any]) -> int:
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
