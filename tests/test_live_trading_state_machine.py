from __future__ import annotations

import unittest

from backend.core.config import settings
from backend.services.live_trading_authorization_service import create_live_authorization
from backend.services.live_trading_session_service import arm_live_strategy, pause_live_strategy, resume_live_strategy, start_live_strategy, stop_live_strategy
from tests.live_control_test_support import seed_live_ready_strategy
from tests.productized_control_plane_test_support import build_test_context


class LiveTradingStateMachineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.context = build_test_context(slug="live-state-test", plan_key="professional")
        self.fixture = seed_live_ready_strategy(self.context)
        self.old_flags = (settings.feature_live_trading, settings.alpaca_live_trading_enabled)

    def tearDown(self) -> None:
        object.__setattr__(settings, "feature_live_trading", self.old_flags[0])
        object.__setattr__(settings, "alpaca_live_trading_enabled", self.old_flags[1])
        self.context.close()

    def _authorization_id(self) -> str:
        return create_live_authorization(
            self.context.db,
            current_user=self.context.current_user,
            request={
                "strategy_id": self.fixture.strategy.id,
                "strategy_version_id": self.fixture.version.id,
                "linked_account_id": self.fixture.account.id,
                "signed": True,
            },
        )["authorization"]["id"]

    def test_arm_then_start_blocks_when_live_feature_flag_is_disabled(self) -> None:
        object.__setattr__(settings, "feature_live_trading", False)
        object.__setattr__(settings, "alpaca_live_trading_enabled", False)
        authorization_id = self._authorization_id()

        armed = arm_live_strategy(self.context.db, current_user=self.context.current_user, strategy_id=self.fixture.strategy.id, request={"authorization_id": authorization_id})
        self.assertEqual(armed["live_state"], "armed")

        started = start_live_strategy(self.context.db, current_user=self.context.current_user, strategy_id=self.fixture.strategy.id, request={})
        self.assertEqual(started["live_state"], "blocked")
        self.assertTrue(any(item["key"] == "live_feature_disabled" for item in started["blockers"]))

    def test_live_session_transitions_when_flags_are_enabled(self) -> None:
        object.__setattr__(settings, "feature_live_trading", True)
        object.__setattr__(settings, "alpaca_live_trading_enabled", True)
        authorization_id = self._authorization_id()

        arm_live_strategy(self.context.db, current_user=self.context.current_user, strategy_id=self.fixture.strategy.id, request={"authorization_id": authorization_id})
        started = start_live_strategy(self.context.db, current_user=self.context.current_user, strategy_id=self.fixture.strategy.id, request={})
        self.assertEqual(started["live_state"], "live")
        self.assertEqual(pause_live_strategy(self.context.db, current_user=self.context.current_user, strategy_id=self.fixture.strategy.id)["live_state"], "paused")
        self.assertEqual(resume_live_strategy(self.context.db, current_user=self.context.current_user, strategy_id=self.fixture.strategy.id)["live_state"], "live")
        self.assertEqual(stop_live_strategy(self.context.db, current_user=self.context.current_user, strategy_id=self.fixture.strategy.id)["live_state"], "stopped")


if __name__ == "__main__":
    unittest.main()
