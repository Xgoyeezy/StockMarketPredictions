from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from backend.services.enterprise_readiness_service import (
    build_enterprise_readiness_snapshot,
    load_validation_tracker_snapshot,
)


class EnterpriseReadinessServiceTests(unittest.TestCase):
    def test_load_validation_tracker_snapshot_reads_export(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "validation_tracker.json"
            path.write_text(
                json.dumps(
                    {
                        "overall_status": "blocked",
                        "status_counts": {"pass": 2, "partial": 3, "fail": 4, "pending": 1},
                        "next_actions": ["Fix execution realism before widening rollout."],
                        "settings_locked": True,
                        "version": "v0",
                    }
                ),
                encoding="utf-8",
            )
            snapshot = load_validation_tracker_snapshot(path)

        self.assertTrue(snapshot["available"])
        self.assertEqual(snapshot["status"], "blocked")
        self.assertEqual(snapshot["status_counts"]["fail"], 4)
        self.assertTrue(snapshot["settings_locked"])
        self.assertEqual(snapshot["next_action"], "Fix execution realism before widening rollout.")

    def test_build_enterprise_readiness_snapshot_blocks_on_validation_and_launch(self) -> None:
        snapshot = build_enterprise_readiness_snapshot(
            readiness_snapshot={"summary": {"status": "ready", "next_action": "Ready."}},
            deployment_snapshot={"summary": {"status": "ready", "next_action": "Ready."}},
            launch_rollup={"summary": {"status": "blocked", "next_action": "Finish tenant launch checklist."}},
            order_lifecycle={"summary": {"status": "ready", "message": "Healthy."}},
            validation_tracker={
                "status": "blocked",
                "next_action": "Fix execution realism before widening rollout.",
                "detail": "Validation is blocked.",
            },
        )

        self.assertEqual(snapshot["summary"]["status"], "blocked")
        self.assertIn("Finish tenant launch checklist.", snapshot["summary"]["blockers"])
        self.assertIn("Fix execution realism before widening rollout.", snapshot["summary"]["blockers"])
        self.assertEqual(snapshot["summary"]["total_checks"], 5)

    def test_build_enterprise_readiness_snapshot_weights_warnings_and_deduplicates_blockers(self) -> None:
        snapshot = build_enterprise_readiness_snapshot(
            readiness_snapshot={"summary": {"status": "blocked", "next_action": "Rotate secrets."}},
            deployment_snapshot={"summary": {"status": "attention", "next_action": "Rotate secrets."}},
            launch_rollup={"summary": {"status": "inactive", "next_action": "Not using white-label launch."}},
            order_lifecycle={"summary": {"status": "healthy", "message": "Healthy."}},
            validation_tracker={"status": "warning", "next_action": "Increase replay coverage."},
        )

        self.assertEqual(snapshot["summary"]["status"], "blocked")
        self.assertEqual(snapshot["summary"]["blockers"], ["Rotate secrets."])
        self.assertIn("Increase replay coverage.", snapshot["summary"]["warnings"])
        self.assertEqual(snapshot["summary"]["readiness_percent"], 60.0)


if __name__ == "__main__":
    unittest.main()
