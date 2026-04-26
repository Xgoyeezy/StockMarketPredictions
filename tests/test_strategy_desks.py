from __future__ import annotations

import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import patch

import pandas as pd
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker


def _resolved_permissions(*, membership_role: str = "owner", platform_role: str = "admin", mode: str = "demo") -> tuple[str, ...]:
    from backend.services.permissions import resolve_user_permissions

    return resolve_user_permissions(
        membership_role=membership_role,
        platform_role=platform_role,
        api_token_scopes=(),
        mode=mode,
    )


def _build_frame(prices: list[float], *, base_volume: float = 1_000_000.0) -> pd.DataFrame:
    index = pd.date_range("2025-01-01", periods=len(prices), freq="D", tz="UTC")
    return pd.DataFrame(
        {
            "Open": [price * 0.998 for price in prices],
            "High": [price * 1.004 for price in prices],
            "Low": [price * 0.996 for price in prices],
            "Close": prices,
            "Volume": [base_volume + (idx * 5000.0) for idx in range(len(prices))],
        },
        index=index,
    )


def _build_market_state(symbols: tuple[str, ...]) -> dict[str, object]:
    base_series = {
        "SPY": [100 + (idx * 0.55) for idx in range(260)],
        "QQQ": [98 + (idx * 0.62) for idx in range(260)],
        "IWM": [80 + (idx * 0.35) for idx in range(260)],
        "TLT": [110 - (idx * 0.05) + ((idx % 10) * 0.01) for idx in range(260)],
        "GLD": [180 + ((idx % 15) * 0.2) + (idx * 0.03) for idx in range(260)],
        "XLE": [70 + (idx * 0.42) for idx in range(260)],
        "XOP": [68 + (idx * 0.38) + ((idx % 7) * 0.04) for idx in range(260)],
        "SMH": [150 + (idx * 0.75) for idx in range(260)],
        "SOXX": [148 + (idx * 0.71) + ((idx % 9) * 0.03) for idx in range(260)],
        "XLF": [35 + (idx * 0.12) for idx in range(260)],
        "KBE": [34 + (idx * 0.11) + ((idx % 11) * 0.02) for idx in range(260)],
    }
    bars = {symbol: _build_frame(base_series[symbol]) for symbol in symbols}
    return {
        "as_of": datetime(2026, 4, 24, 16, 0, tzinfo=timezone.utc).isoformat(),
        "provider_name": "test_provider",
        "requirements": [],
        "bars": bars,
    }


class StrategyDeskPlatformTests(unittest.TestCase):
    def setUp(self) -> None:
        import backend.models.saas  # noqa: F401
        from backend.core.database import Base

        self.engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
        self.Session = sessionmaker(bind=self.engine, expire_on_commit=False, future=True)
        Base.metadata.create_all(bind=self.engine)
        self.db = self.Session()
        self.addCleanup(self.engine.dispose)
        self.addCleanup(self.db.close)

        from backend.services import tenant_service

        fake_settings = SimpleNamespace(
            demo_tenant_slug="alpha-desk",
            demo_tenant_name="Alpha Desk",
            demo_tenant_plan="pro",
        )
        with patch.object(tenant_service, "settings", fake_settings):
            identity = tenant_service.ensure_demo_tenant_for_user(
                self.db,
                auth_subject="demo-strategy-user",
                email="demo-strategy@example.test",
                name="Demo Strategy User",
                provider="local-demo",
                requested_tenant_slug="alpha-desk",
            )

        self.identity = identity
        self.current_user = SimpleNamespace(
            tenant_id=identity["active_tenant"].id,
            auth_subject="demo-strategy-user",
            role="owner",
            platform_role="admin",
            mode="demo",
            permissions=_resolved_permissions(membership_role="owner", platform_role="admin"),
        )

    def test_strategy_desk_registry_seeds_all_internal_desks(self) -> None:
        from backend.services.strategy_engine import service as strategy_service
        from backend.services.strategy_engine.registry import build_strategy_desk, list_strategy_desk_definitions

        payload = strategy_service.list_strategy_desks(self.db, current_user=self.current_user)

        self.assertEqual(payload["count"], 5)
        desk_keys = {item["desk_key"] for item in payload["items"]}
        self.assertEqual(
            desk_keys,
            {"macro_trend", "stat_arb", "equities_momentum", "event_driven", "options_volatility"},
        )
        macro = next(item for item in payload["items"] if item["desk_key"] == "macro_trend")
        stat_arb = next(item for item in payload["items"] if item["desk_key"] == "stat_arb")
        self.assertTrue(macro["paper_trading_enabled"])
        self.assertEqual(macro["lifecycle_stage"], "paper")
        self.assertFalse(next(item for item in payload["items"] if item["desk_key"] == "options_volatility")["paper_trading_enabled"])
        self.assertEqual(stat_arb["trading_mode"], "paper")
        for definition in list_strategy_desk_definitions():
            self.assertTrue(definition.default_config["include_extended_hours"])
            self.assertEqual(
                set(definition.default_config["session_profiles"]),
                {"pre_market", "regular", "after_hours", "closed"},
            )
            self.assertEqual(definition.default_config["session_profiles"]["pre_market"]["time_in_force"], "day_ext")
            self.assertEqual(definition.default_config["session_profiles"]["after_hours"]["order_type"], "limit")
            self.assertFalse(definition.default_config["session_profiles"]["closed"]["allow_new_entries"])
            desk = build_strategy_desk(definition.key)
            self.assertTrue(all(requirement.prepost for requirement in desk.get_data_requirements()))

    def test_strategy_desk_run_backtest_and_allocator_snapshots_persist(self) -> None:
        from backend.services.strategy_engine import service as strategy_service

        strategy_service.update_strategy_desk(
            self.db,
            current_user=self.current_user,
            desk_key="macro_trend",
            updates={"config": {"universe": ["SPY", "QQQ", "IWM"], "max_positions": 2}},
        )
        strategy_service.update_strategy_desk(
            self.db,
            current_user=self.current_user,
            desk_key="stat_arb",
            updates={"config": {"pairs": [["SPY", "QQQ"]], "max_pairs": 1}},
        )

        market_state = _build_market_state(("SPY", "QQQ", "IWM"))
        with (
            patch("backend.services.feature_store.service.load_market_state", return_value=market_state),
            patch("backend.services.backtesting.service.load_market_state", return_value=market_state),
            patch("backend.services.strategy_engine.service.record_audit_event", return_value=None),
        ):
            macro_run = strategy_service.run_strategy_desk(self.db, current_user=self.current_user, desk_key="macro_trend")
            stat_arb_run = strategy_service.run_strategy_desk(self.db, current_user=self.current_user, desk_key="stat_arb")
            backtest = strategy_service.run_backtest_for_desk(
                self.db,
                current_user=self.current_user,
                desk_key="macro_trend",
                request_payload={"horizon_days": 5, "warmup_bars": 80, "fee_bps": 2, "slippage_bps": 5},
            )
            allocator = strategy_service.build_allocator_snapshot(self.db, current_user=self.current_user)
            latest_targets = strategy_service.get_latest_portfolio_targets(self.db, current_user=self.current_user)
            desks = strategy_service.list_strategy_desks(self.db, current_user=self.current_user)

        self.assertEqual(macro_run["run"]["status"], "accepted")
        self.assertGreaterEqual(macro_run["run"]["target_count"], 1)
        self.assertEqual(stat_arb_run["run"]["status"], "accepted")
        self.assertGreaterEqual(stat_arb_run["run"]["target_count"], 2)
        self.assertEqual(backtest["status"], "completed")
        self.assertGreater(backtest["summary"]["trade_count"], 0)
        self.assertEqual(allocator["status"], "accepted")
        self.assertGreaterEqual(allocator["metrics"]["target_count"], 1)
        self.assertEqual(latest_targets["latest_run_id"], allocator["latest_run_id"])
        self.assertEqual(len(latest_targets["targets"]), allocator["metrics"]["target_count"])

        macro_snapshot = next(item for item in desks["items"] if item["desk_key"] == "macro_trend")
        self.assertIsNotNone(macro_snapshot["latest_run"])
        self.assertEqual(macro_snapshot["latest_run"]["status"], "accepted")
        self.assertIsNotNone(macro_snapshot["latest_backtest"])
        self.assertEqual(macro_snapshot["latest_backtest"]["status"], "completed")

    def test_strategy_desk_metrics_only_include_requested_desk_events(self) -> None:
        from backend.services.strategy_engine import service as strategy_service

        strategy_service.update_strategy_desk(
            self.db,
            current_user=self.current_user,
            desk_key="macro_trend",
            updates={"config": {"universe": ["SPY", "QQQ", "IWM"], "max_positions": 2}},
        )
        strategy_service.update_strategy_desk(
            self.db,
            current_user=self.current_user,
            desk_key="stat_arb",
            updates={"config": {"pairs": [["SPY", "QQQ"]], "max_pairs": 1}},
        )

        market_state = _build_market_state(("SPY", "QQQ", "IWM"))
        with (
            patch("backend.services.feature_store.service.load_market_state", return_value=market_state),
            patch("backend.services.backtesting.service.load_market_state", return_value=market_state),
            patch("backend.services.strategy_engine.service.record_audit_event", return_value=None),
        ):
            strategy_service.run_strategy_desk(self.db, current_user=self.current_user, desk_key="macro_trend")
            strategy_service.run_strategy_desk(self.db, current_user=self.current_user, desk_key="stat_arb")
            strategy_service.run_backtest_for_desk(
                self.db,
                current_user=self.current_user,
                desk_key="macro_trend",
                request_payload={"horizon_days": 5, "warmup_bars": 80},
            )
            metrics = strategy_service.get_strategy_desk_metrics(
                self.db,
                current_user=self.current_user,
                desk_key="macro_trend",
            )

        self.assertEqual(metrics["desk_key"], "macro_trend")
        self.assertTrue(metrics["events"])
        serialized = " ".join(str(item) for item in metrics["events"])
        self.assertIn("macro_trend", serialized)
        self.assertNotIn("stat_arb", serialized)


if __name__ == "__main__":
    unittest.main()
