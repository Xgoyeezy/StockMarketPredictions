from __future__ import annotations

import importlib
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


class AlembicMigrationTests(unittest.TestCase):
    def test_live_migration_modules_have_upgrade_and_downgrade(self) -> None:
        for module_name in [
            "backend.migrations.versions.007_live_trading_core",
            "backend.migrations.versions.008_live_execution_controls",
        ]:
            module = importlib.import_module(module_name)
            self.assertTrue(callable(module.upgrade))
            self.assertTrue(callable(module.downgrade))

    def test_sqlite_upgrade_head_and_downgrade_base(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "alembic-live.db"
            env = dict(os.environ)
            env["DATABASE_URL"] = f"sqlite:///{db_path.as_posix()}"
            upgrade = subprocess.run(
                [sys.executable, "-m", "alembic", "-c", "alembic.ini", "upgrade", "head"],
                cwd=Path(__file__).resolve().parents[1],
                env=env,
                text=True,
                capture_output=True,
                timeout=60,
            )
            self.assertEqual(upgrade.returncode, 0, upgrade.stderr + upgrade.stdout)
            downgrade = subprocess.run(
                [sys.executable, "-m", "alembic", "-c", "alembic.ini", "downgrade", "base"],
                cwd=Path(__file__).resolve().parents[1],
                env=env,
                text=True,
                capture_output=True,
                timeout=60,
            )
            self.assertEqual(downgrade.returncode, 0, downgrade.stderr + downgrade.stdout)


if __name__ == "__main__":
    unittest.main()
