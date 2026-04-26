from __future__ import annotations

import ast
from pathlib import Path
import unittest


class ImportIsolationTest(unittest.TestCase):
    def test_hft_package_does_not_import_main_platform(self) -> None:
        root = Path(__file__).resolve().parents[1] / "hft"
        forbidden_prefixes = ("backend", "frontend")
        offenders: list[str] = []

        for path in root.rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        if alias.name.startswith(forbidden_prefixes):
                            offenders.append(f"{path}: import {alias.name}")
                elif isinstance(node, ast.ImportFrom):
                    module = node.module or ""
                    if module.startswith(forbidden_prefixes):
                        offenders.append(f"{path}: from {module}")

        self.assertEqual(offenders, [])
