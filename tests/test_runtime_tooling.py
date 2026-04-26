from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from io import StringIO
from unittest.mock import patch

from scripts.check_options_paper_readiness import classify_readiness
from scripts.manage_api_runtime import (
    _parse_windows_netstat_pids,
    _start_detached_windows,
    normalize_runtime_probe_host,
    runtime_name_for_env_file,
)
from scripts.backend_test_groups import unittest_names_for_group
from scripts.print_staging_boot_command import main as print_staging_boot_command_main
from scripts.validate_staging_env import validate_env


class RuntimeToolingTests(unittest.TestCase):
    def test_runtime_name_for_local_and_staging_env_files(self) -> None:
        self.assertEqual(runtime_name_for_env_file(Path(".env")), "local-api")
        self.assertEqual(runtime_name_for_env_file(Path(".env.staging")), "staging-api")
        self.assertEqual(runtime_name_for_env_file(Path("custom.env.qa")), "custom-env-qa-api")

    def test_normalize_runtime_probe_host_prefers_ipv4_loopback_for_local_hosts(self) -> None:
        self.assertEqual(normalize_runtime_probe_host(None), "127.0.0.1")
        self.assertEqual(normalize_runtime_probe_host(""), "127.0.0.1")
        self.assertEqual(normalize_runtime_probe_host("localhost"), "127.0.0.1")
        self.assertEqual(normalize_runtime_probe_host("0.0.0.0"), "127.0.0.1")
        self.assertEqual(normalize_runtime_probe_host("::"), "127.0.0.1")
        self.assertEqual(normalize_runtime_probe_host("192.168.1.25"), "192.168.1.25")

    def test_parse_windows_netstat_pids_filters_port_and_listener_state(self) -> None:
        raw = """
  TCP    0.0.0.0:8000           0.0.0.0:0              LISTENING       14248
  TCP    127.0.0.1:5173         0.0.0.0:0              LISTENING       9920
  TCP    [::]:8000              [::]:0                 LISTENING       14248
  TCP    127.0.0.1:8000         127.0.0.1:54000        ESTABLISHED     14248
"""
        self.assertEqual(_parse_windows_netstat_pids(raw, 8000), [14248])
        self.assertEqual(_parse_windows_netstat_pids(raw, 5173), [9920])

    def test_backend_group_runner_uses_discoverable_test_module_names(self) -> None:
        names = unittest_names_for_group("ops-readiness")

        self.assertTrue(names)
        self.assertTrue(all(name.startswith("test_backend_behaviors.BackendBehaviorTests.") for name in names))

    def test_classify_readiness_surfaces_opra_entitlement_gap(self) -> None:
        result = classify_readiness(
            feed="opra",
            use_sandbox=False,
            paper_keys_present=True,
            opra_probe={"status_code": 403, "message": "subscription does not permit querying OPRA data"},
            indicative_probe={"status_code": 200, "message": None},
            backend_running=True,
        )
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["broker_code"], "opra_not_entitled")

    def test_classify_readiness_reports_backend_not_running_after_broker_ready(self) -> None:
        result = classify_readiness(
            feed="opra",
            use_sandbox=False,
            paper_keys_present=True,
            opra_probe={"status_code": 200, "message": None},
            indicative_probe={"status_code": 200, "message": None},
            backend_running=False,
            env_file_name=".env.staging",
        )
        self.assertEqual(result["status"], "warning")
        self.assertEqual(result["broker_code"], "ready")
        self.assertEqual(result["backend_code"], "backend_not_running")
        self.assertIn(".env.staging", result["next_action"])

    def test_validate_staging_env_rejects_sandbox_options_configuration(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            env_path = Path(tmpdir) / ".env.staging"
            env_path.write_text(
                "\n".join(
                    [
                        "APP_ENV=staging",
                        "API_PORT=8001",
                        "API_RELOAD=false",
                        "AUTH_ENABLED=true",
                        "ALLOW_DEMO_AUTH=false",
                        "AUTH_PROVIDER=local-session",
                        "DATABASE_URL=postgresql+psycopg://user:pass@localhost:54329/staging",
                        "AUTH_SESSION_SECRET=real-secret",
                        "AUTH_STATE_SECRET=real-secret",
                        "API_TOKEN_SALT=real-secret",
                        "MARKET_DATA_PROVIDER=alpaca",
                        "APCA_API_KEY_ID=key",
                        "APCA_API_SECRET_KEY=secret",
                        "ALPACA_USE_SANDBOX=true",
                        "ALPACA_OPTIONS_FEED=indicative",
                        "PUBLIC_API_BASE_URL=http://localhost:8001/api",
                        "RUNTIME_API_BASE_URL=http://127.0.0.1:8001/api",
                        "ALLOW_ORIGINS=http://localhost:5173",
                        "STAGING_BILLING_MODE=disabled",
                        "STAGING_ACCESS_MODE=local",
                    ]
                ),
                encoding="utf-8",
            )
            blockers, warnings = validate_env(env_path)

        self.assertIn("ALPACA_USE_SANDBOX must be set to 'false'.", blockers)
        self.assertIn("ALPACA_OPTIONS_FEED must be set to 'opra'.", blockers)
        self.assertEqual(warnings, [])

    def test_print_staging_boot_command_emits_full_staging_runbook(self) -> None:
        output = StringIO()
        with patch("sys.stdout", output):
            exit_code = print_staging_boot_command_main()

        rendered = output.getvalue()
        self.assertEqual(exit_code, 0)
        self.assertIn("-Action db-up", rendered)
        self.assertIn("-Action use-local-db", rendered)
        self.assertIn("start --env-file .env.staging", rendered)
        self.assertIn("check_options_paper_readiness.py", rendered)

    def test_start_detached_windows_uses_hidden_cmd_wrapper_and_returns_pid(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            stdout_path = tmp_path / "runtime.out.log"
            stderr_path = tmp_path / "runtime.err.log"
            python_path = tmp_path / "python.exe"
            python_path.write_text("", encoding="utf-8")
            config = {
                "python_path": python_path,
                "root": tmp_path,
                "env_values": {"FOO": "bar"},
                "env_path": tmp_path / ".env",
                "stdout_path": stdout_path,
                "stderr_path": stderr_path,
            }
            config["env_path"].write_text("FOO=bar\n", encoding="utf-8")

            with patch("scripts.manage_api_runtime.subprocess.run") as run_mock:
                run_mock.return_value.returncode = 0
                run_mock.return_value.stdout = "4321\n"
                run_mock.return_value.stderr = ""
                pid = _start_detached_windows(config)

            self.assertEqual(pid, 4321)
            run_args, run_kwargs = run_mock.call_args
            self.assertEqual(Path(run_kwargs["cwd"]), tmp_path)
            command = run_args[0]
            self.assertIn("powershell", command[0])
            command_text = command[-1]
            self.assertIn("Start-Process", command_text)
            self.assertIn("$env:ComSpec", command_text)
            self.assertIn("-WindowStyle Hidden", command_text)
            self.assertIn(str(python_path), command_text)
            self.assertIn(str(config["env_path"]), command_text)
            self.assertIn(str(stdout_path), command_text)
            self.assertIn(str(stderr_path), command_text)


if __name__ == "__main__":
    unittest.main()
