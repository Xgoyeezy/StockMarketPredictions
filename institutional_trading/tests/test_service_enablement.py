from __future__ import annotations

import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from institutional_trading.cli import main


class ServiceEnablementTest(unittest.TestCase):
    def test_cli_rejects_live_or_non_paper_config(self):
        with tempfile.TemporaryDirectory() as tmp:
            live_config, _ = write_config(Path(tmp), live=True)
            disabled_config, _ = write_config(Path(tmp), paper=False, name="disabled.yaml")
            self.assertEqual(run_cli(["--config", str(live_config), "status"])[0], 2)
            self.assertEqual(run_cli(["--config", str(disabled_config), "status"])[0], 2)

    def test_cli_run_writes_status_health_and_audit(self):
        with tempfile.TemporaryDirectory() as tmp:
            config, runtime = write_config(Path(tmp))
            code, _, err = run_cli(["--config", str(config), "run", "--heartbeat-interval", "0.01", "--max-heartbeats", "1"])
            self.assertEqual(code, 0, err)
            status = json.loads((runtime / "status.json").read_text(encoding="utf-8"))
            health = json.loads((runtime / "health.json").read_text(encoding="utf-8"))
            audit_log = runtime / "audit" / "events.jsonl"
            self.assertFalse(status["running"])
            self.assertEqual(status["mode"], "paper")
            self.assertTrue(status["paper_safe"])
            self.assertIn("aggregate", health)
            self.assertIn("service_start", audit_log.read_text(encoding="utf-8"))
            self.assertIn("service_stop", audit_log.read_text(encoding="utf-8"))

    def test_cli_kill_sets_failed_health_without_broker_submission(self):
        with tempfile.TemporaryDirectory() as tmp:
            config, runtime = write_config(Path(tmp))
            self.assertEqual(run_cli(["--config", str(config), "start"])[0], 0)
            code, _, err = run_cli(["--config", str(config), "kill", "--reason", "unit_test"])
            self.assertEqual(code, 0, err)
            status = json.loads((runtime / "status.json").read_text(encoding="utf-8"))
            health = json.loads((runtime / "health.json").read_text(encoding="utf-8"))
            self.assertEqual(status["health"]["status"], "failed")
            self.assertEqual(health["aggregate"]["status"], "failed")
            self.assertTrue((runtime / "kill_switch.json").exists())

    def test_process_manager_start_status_stop(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = repo_root()
            config, runtime = write_config(Path(tmp))
            log_dir = Path(tmp) / "logs"
            env = os.environ.copy()
            env["INSTITUTIONAL_TRADING_PYTHON"] = sys.executable
            env["PYTHONPATH"] = prepend_path(str(root), env.get("PYTHONPATH"))
            base = [
                sys.executable,
                "scripts/manage_institutional_trading.py",
                "--config",
                str(config),
                "--runtime-dir",
                str(runtime),
                "--log-dir",
                str(log_dir),
                "--heartbeat-interval",
                "0.1",
                "--timeout",
                "10",
            ]
            try:
                start = subprocess.run(base + ["start"], cwd=root, env=env, text=True, capture_output=True, timeout=20)
                self.assertEqual(start.returncode, 0, start.stderr)
                self.assertEqual(json.loads(start.stdout)["status"], "started")
                status = subprocess.run(base + ["status"], cwd=root, env=env, text=True, capture_output=True, timeout=20)
                self.assertEqual(status.returncode, 0, status.stderr)
                payload = json.loads(status.stdout)
                self.assertTrue(payload["process_running"])
                self.assertTrue(payload["service"]["running"])
            finally:
                stop = subprocess.run(base + ["stop"], cwd=root, env=env, text=True, capture_output=True, timeout=20)
            self.assertEqual(stop.returncode, 0, stop.stderr)
            stopped = json.loads(stop.stdout)
            self.assertFalse(stopped["process_running"])
            self.assertFalse(stopped["service"]["running"])


def run_cli(args: list[str]) -> tuple[int, str, str]:
    out = io.StringIO()
    err = io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = main(args)
    return code, out.getvalue(), err.getvalue()


def write_config(base: Path, *, live: bool = False, paper: bool = True, name: str = "config.yaml") -> tuple[Path, Path]:
    runtime = base / name.replace(".yaml", "") / "runtime"
    audit = runtime / "audit"
    config = base / name
    config.write_text(
        "\n".join(
            [
                "service:",
                f"  runtime_dir: {as_yaml_path(runtime)}",
                "  watchdog: {enabled: true, max_failed_checks: 2, heartbeat_interval_seconds: 0.1}",
                "broker:",
                "  primary: ibkr",
                f"  live_trading_enabled: {str(live).lower()}",
                "  ibkr: {host: 127.0.0.1, port: 7497, client_id: 11}",
                f"  paper: {{enabled: {str(paper).lower()}}}",
                "symbols: {restricted: []}",
                "risk:",
                "  max_order_quantity: 10",
                "  max_position_size: 20",
                "  max_symbol_exposure: 30",
                "  max_gross_exposure: 100000.0",
                "  max_daily_loss: 1000.0",
                "  max_drawdown: 2000.0",
                "audit:",
                f"  jsonl_path: {as_yaml_path(audit / 'events.jsonl')}",
                f"  sqlite_index_path: {as_yaml_path(audit / 'events.sqlite3')}",
                "",
            ]
        ),
        encoding="utf-8",
    )
    return config, runtime


def as_yaml_path(path: Path) -> str:
    return str(path).replace("\\", "/")


def repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def prepend_path(value: str, current: str | None) -> str:
    if not current:
        return value
    return value + os.pathsep + current


if __name__ == "__main__":
    unittest.main()
