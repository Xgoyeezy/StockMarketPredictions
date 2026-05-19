from __future__ import annotations

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class RiskAuditHardeningFrontendStaticTests(unittest.TestCase):
    def test_risk_center_renders_hardening_report_and_tracker(self) -> None:
        page = (ROOT / "frontend" / "src" / "pages" / "RiskCenterPage.jsx").read_text(encoding="utf-8")
        client = (ROOT / "frontend" / "src" / "api" / "client.js").read_text(encoding="utf-8")

        self.assertIn("Risk And Audit Hardening Plan", page)
        self.assertIn("Audit Boundary Notes", page)
        self.assertIn("No proof-layer bypass", page)
        self.assertIn("Does not clear kill switches", page)
        self.assertIn("FinishTrackerSection", page)
        self.assertIn("getRiskAuditHardening", page)
        self.assertIn("getKillSwitchStatus", page)
        self.assertIn("active={Boolean(killSwitch?.active)}", page)
        self.assertNotIn("active={events.some((item) => item.event_type?.includes('kill'))}", page)
        self.assertIn("risk_audit_hardening_plan", client)
        self.assertIn("FALLBACK_RISK_AUDIT_HARDENING", client)
        self.assertIn("FALLBACK_RISK_KILL_SWITCH", client)
        self.assertIn("Active risk policy evidence", client)
        self.assertIn("Kill-switch auditability", client)
        self.assertIn("risk/kill-switch", client)
        self.assertIn("Sanitized export boundary", client)
        self.assertIn("can_bypass_risk_gates: false", client)
        self.assertIn("can_clear_kill_switch: false", client)
        self.assertIn("live_trading_readiness", client)


if __name__ == "__main__":
    unittest.main()
