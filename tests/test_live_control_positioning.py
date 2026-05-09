from __future__ import annotations

import unittest
from pathlib import Path

from backend.core.config import settings


ROOT = Path(__file__).resolve().parents[1]


class LiveControlPositioningTests(unittest.TestCase):
    def test_native_broker_flag_is_removed_from_runtime_config(self) -> None:
        self.assertFalse(hasattr(settings, "enable_proprietary_broker"))

    def test_docs_and_env_do_not_advertise_native_broker_mode(self) -> None:
        paths = [
            ROOT / ".env.example",
            ROOT / "docker-compose.yml",
            ROOT / "README.md",
            ROOT / "docs" / "broker_trading_desk_architecture.md",
            ROOT / "docs" / "compliance_checklist.md",
        ]
        combined = "\n".join(path.read_text(encoding="utf-8") for path in paths)
        forbidden = [
            "ENABLE_PROPRIETARY_BROKER",
            "proprietary broker",
            "native broker",
            "Native Broker Mode",
            "premium brokerage platform",
        ]
        for phrase in forbidden:
            self.assertNotIn(phrase.lower(), combined.lower())


if __name__ == "__main__":
    unittest.main()
