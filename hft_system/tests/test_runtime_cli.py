from __future__ import annotations

from pathlib import Path
import unittest

from hft.runtime import cli


class RuntimeCliTest(unittest.TestCase):
    def test_resolve_path_falls_back_to_hft_project_root(self) -> None:
        resolved = cli._resolve_path("configs/symbols.yaml")
        self.assertTrue(resolved.is_absolute())
        self.assertEqual(resolved, cli.PROJECT_ROOT / "configs" / "symbols.yaml")
        self.assertTrue(resolved.exists())


if __name__ == "__main__":
    unittest.main()
