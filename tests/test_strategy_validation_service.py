from __future__ import annotations

import json
import tempfile
import unittest
import warnings
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

import pandas as pd

from backend.services import strategy_validation_service as svs


class StrategyValidationServiceTests(unittest.TestCase):
    def test_trade_validation_ledger_avoids_concat_futurewarning_with_sparse_entry_frames(self) -> None:
        open_trades = pd.DataFrame(
            [
                {
                    "trade_id": None,
                    "ticker": None,
                    "opened_at": None,
                }
            ]
        )

        with (
            patch.object(svs, "_load_tenant_row", return_value={"id": "tenant-1", "slug": "alpha-desk", "name": "Alpha Desk", "metadata": {}}),
            patch.object(
                svs,
                "_load_trade_frames",
                return_value={
                    "open_trades": open_trades,
                    "closed_trades": pd.DataFrame(),
                    "pending_orders": pd.DataFrame(),
                    "forecast_journal": pd.DataFrame(),
                    "trade_journal": pd.DataFrame(),
                },
            ),
            patch.object(svs, "_load_order_events", return_value=pd.DataFrame()),
            warnings.catch_warnings(record=True) as caught,
        ):
            warnings.simplefilter("always")
            ledger = svs.build_trade_validation_ledger(tenant_slug="alpha-desk", starting_capital=100000.0)

        self.assertFalse(any(item.category is FutureWarning for item in caught))

    def test_trade_validation_ledger_includes_explicit_accounting_fields(self) -> None:
        closed_trades = pd.DataFrame(
            [
                {
                    "trade_id": "trade-1",
                    "ticker": "MSFT",
                    "interval": "5m",
                    "instrument_type": "equity",
                    "suggested_contracts": 10.0,
                    "closed_contracts": 10.0,
                    "position_cost": 1000.0,
                    "max_risk_dollars": 25.0,
                    "opened_at": "2026-04-20T17:00:00+00:00",
                    "submitted_at": "2026-04-20T16:59:00+00:00",
                    "closed_at": "2026-04-20T17:10:00+00:00",
                    "live_price_at_open": 100.0,
                    "live_price_at_close": 101.0,
                    "actual_fill_price": 100.0,
                    "fill_slippage_dollars": 1.5,
                    "fees": 2.0,
                    "realized_pnl": 10.0,
                    "trade_decision": "VALID TRADE",
                    "setup_grade": "A",
                    "alignment_label": "ALIGNED",
                    "conviction_label": "HIGH",
                    "verdict": "BULLISH",
                    "order_type": "market",
                    "status": "CLOSED",
                }
            ]
        )
        forecast_journal = pd.DataFrame(
            [
                {
                    "ticker": "MSFT",
                    "interval": "5m",
                    "forecast_at": "2026-04-20T16:58:30+00:00",
                }
            ]
        )
        order_events = pd.DataFrame(
            [
                {"trade_id": "trade-1", "event_key": "order.submitted", "created_at": pd.Timestamp("2026-04-20T16:59:00+00:00")},
                {"trade_id": "trade-1", "event_key": "order.filled", "created_at": pd.Timestamp("2026-04-20T17:00:00+00:00")},
                {"trade_id": "trade-1", "event_key": "order.closed", "created_at": pd.Timestamp("2026-04-20T17:10:00+00:00")},
            ]
        )

        with (
            patch.object(svs, "_load_tenant_row", return_value={"id": "tenant-1", "slug": "alpha-desk", "name": "Alpha Desk", "metadata": {}}),
            patch.object(
                svs,
                "_load_trade_frames",
                return_value={
                    "open_trades": pd.DataFrame(),
                    "closed_trades": closed_trades,
                    "pending_orders": pd.DataFrame(),
                    "forecast_journal": forecast_journal,
                    "trade_journal": pd.DataFrame(),
                },
            ),
            patch.object(svs, "_load_order_events", return_value=order_events),
        ):
            ledger = svs.build_trade_validation_ledger(tenant_slug="alpha-desk", starting_capital=100000.0)

        self.assertTrue(
            {
                "borrowed_amount",
                "margin_interest",
                "cumulative_margin_interest",
                "long_market_value",
                "short_market_value",
                "route_family",
                "route_version",
                "automation_entry_reason",
                "thesis_direction",
                "directional_exposure",
                "validation_sample_bucket",
            }.issubset(ledger.columns)
        )
        self.assertEqual(float(ledger.iloc[0]["borrowed_amount"]), 0.0)
        self.assertEqual(float(ledger.iloc[0]["fees"]), 2.0)
        self.assertEqual(float(ledger.iloc[-1]["cumulative_fees"]), 4.0)
        self.assertEqual(str(ledger.iloc[0]["route_family"]), "legacy")
        self.assertEqual(str(ledger.iloc[0]["validation_sample_bucket"]), "legacy")

    def test_partial_close_does_not_duplicate_fill_event(self) -> None:
        closed_trades = pd.DataFrame(
            [
                {
                    "trade_id": "trade-1",
                    "ticker": "AMD",
                    "interval": "5m",
                    "instrument_type": "equity",
                    "suggested_contracts": 10.0,
                    "closed_contracts": 9.0,
                    "position_cost": 1000.0,
                    "max_risk_dollars": 25.0,
                    "opened_at": "2026-04-20T17:00:00+00:00",
                    "submitted_at": "2026-04-20T16:59:00+00:00",
                    "closed_at": "2026-04-20T17:10:00+00:00",
                    "live_price_at_open": 100.0,
                    "live_price_at_close": 101.0,
                    "actual_fill_price": 100.0,
                    "fill_slippage_dollars": 0.0,
                    "realized_pnl": 9.0,
                    "trade_decision": "PASS",
                    "setup_grade": "Avoid",
                    "alignment_label": "MIXED",
                    "conviction_label": "LOW",
                    "verdict": "BEARISH",
                    "order_type": "market",
                    "status": "PARTIAL",
                }
            ]
        )
        open_trades = pd.DataFrame(
            [
                {
                    "trade_id": "trade-1",
                    "ticker": "AMD",
                    "interval": "5m",
                    "instrument_type": "equity",
                    "suggested_contracts": 1.0,
                    "position_cost": 100.0,
                    "max_risk_dollars": 2.5,
                    "opened_at": "2026-04-20T17:00:00+00:00",
                    "submitted_at": "2026-04-20T16:59:00+00:00",
                    "live_price_at_open": 100.0,
                    "actual_fill_price": 100.0,
                    "trade_decision": "PASS",
                    "setup_grade": "Avoid",
                    "alignment_label": "MIXED",
                    "conviction_label": "LOW",
                    "verdict": "BEARISH",
                    "order_type": "market",
                    "status": "OPEN",
                }
            ]
        )
        forecast_journal = pd.DataFrame(
            [
                {
                    "ticker": "AMD",
                    "interval": "5m",
                    "forecast_at": "2026-04-20T16:58:30+00:00",
                }
            ]
        )
        order_events = pd.DataFrame(
            [
                {"trade_id": "trade-1", "event_key": "order.submitted", "created_at": pd.Timestamp("2026-04-20T16:59:00+00:00")},
                {"trade_id": "trade-1", "event_key": "order.filled", "created_at": pd.Timestamp("2026-04-20T17:00:00+00:00")},
                {"trade_id": "trade-1", "event_key": "order.closed", "created_at": pd.Timestamp("2026-04-20T17:10:00+00:00")},
            ]
        )

        with (
            patch.object(svs, "_load_tenant_row", return_value={"id": "tenant-1", "slug": "alpha-desk", "name": "Alpha Desk", "metadata": {}}),
            patch.object(
                svs,
                "_load_trade_frames",
                return_value={
                    "open_trades": open_trades,
                    "closed_trades": closed_trades,
                    "pending_orders": pd.DataFrame(),
                    "forecast_journal": forecast_journal,
                    "trade_journal": pd.DataFrame(),
                },
            ),
            patch.object(svs, "_load_order_events", return_value=order_events),
        ):
            ledger = svs.build_trade_validation_ledger(tenant_slug="alpha-desk", starting_capital=100000.0)

        self.assertEqual(list(ledger["event_type"]), ["fill", "close"])
        self.assertEqual(int((ledger["event_type"] == "fill").sum()), 1)
        self.assertEqual(int((ledger["event_type"] == "close").sum()), 1)
        self.assertEqual(float(ledger.iloc[-1]["position_after"]), 1.0)

    def test_trade_validation_ledger_backfills_current_route_metadata_from_order_events(self) -> None:
        closed_trades = pd.DataFrame(
            [
                {
                    "trade_id": "trade-put-1",
                    "ticker": "QQQ",
                    "interval": "5m",
                    "instrument_type": "listed_option",
                    "suggested_contracts": 1.0,
                    "closed_contracts": 1.0,
                    "position_cost": 250.0,
                    "max_risk_dollars": 50.0,
                    "opened_at": "2026-04-20T17:00:00+00:00",
                    "submitted_at": "2026-04-20T16:59:00+00:00",
                    "closed_at": "2026-04-20T17:10:00+00:00",
                    "live_price_at_open": 2.5,
                    "live_price_at_close": 3.0,
                    "actual_fill_price": 2.5,
                    "fill_slippage_dollars": 0.0,
                    "realized_pnl": 50.0,
                    "trade_decision": "PASS",
                    "setup_grade": "Avoid",
                    "alignment_label": "MIXED",
                    "conviction_label": "LOW",
                    "verdict": "BEARISH",
                    "order_type": "market",
                    "status": "CLOSED",
                }
            ]
        )
        forecast_journal = pd.DataFrame(
            [
                {
                    "ticker": "QQQ",
                    "interval": "5m",
                    "forecast_at": "2026-04-20T16:58:30+00:00",
                }
            ]
        )
        order_events = pd.DataFrame(
            [
                {
                    "trade_id": "trade-put-1",
                    "event_key": "order.submitted",
                    "created_at": pd.Timestamp("2026-04-20T16:59:00+00:00"),
                    "payload_json": {
                        "request": {
                            "route_family": "current",
                            "route_version": "ranked_entry_v1",
                            "automation_entry_reason": "ranked_candidate",
                            "thesis_direction": "BEARISH",
                            "instrument_type": "listed_option",
                            "option_right": "put",
                            "validation_sample_bucket": "current_route",
                        }
                    },
                },
                {
                    "trade_id": "trade-put-1",
                    "event_key": "order.filled",
                    "created_at": pd.Timestamp("2026-04-20T17:00:00+00:00"),
                    "payload_json": {},
                },
                {
                    "trade_id": "trade-put-1",
                    "event_key": "order.closed",
                    "created_at": pd.Timestamp("2026-04-20T17:10:00+00:00"),
                    "payload_json": {},
                },
            ]
        )

        with (
            patch.object(svs, "_load_tenant_row", return_value={"id": "tenant-1", "slug": "alpha-desk", "name": "Alpha Desk", "metadata": {}}),
            patch.object(
                svs,
                "_load_trade_frames",
                return_value={
                    "open_trades": pd.DataFrame(),
                    "closed_trades": closed_trades,
                    "pending_orders": pd.DataFrame(),
                    "forecast_journal": forecast_journal,
                    "trade_journal": pd.DataFrame(),
                },
            ),
            patch.object(svs, "_load_order_events", return_value=order_events),
        ):
            ledger = svs.build_trade_validation_ledger(tenant_slug="alpha-desk", starting_capital=100000.0)

        self.assertEqual(list(ledger["event_type"]), ["fill", "close"])
        self.assertTrue((ledger["route_family"] == "current").all())
        self.assertTrue((ledger["route_version"] == "ranked_entry_v1").all())
        self.assertTrue((ledger["automation_entry_reason"] == "ranked_candidate").all())
        self.assertTrue((ledger["thesis_direction"] == "BEARISH").all())
        self.assertTrue((ledger["directional_exposure"] == "bearish").all())
        self.assertTrue((ledger["validation_sample_bucket"] == "current_route").all())
        self.assertTrue(str(ledger.iloc[0]["reason_for_entry"]).startswith("PASS |"))

    def test_signal_execution_alignment_flags_bearish_buy_fill(self) -> None:
        ledger = pd.DataFrame(
            [
                {
                    "timestamp": "2026-04-20T17:00:00+00:00",
                    "event_type": "fill",
                    "trade_id": "trade-1",
                    "symbol": "MSFT",
                    "side": "BUY",
                    "signal_verdict": "BEARISH",
                    "reason_for_entry": "PASS | Avoid | BEARISH BIAS | LOW CONVICTION",
                    "instrument_type": "equity",
                    "directional_exposure": "bullish",
                    "validation_sample_bucket": "legacy",
                },
                {
                    "timestamp": "2026-04-20T17:05:00+00:00",
                    "event_type": "fill",
                    "trade_id": "trade-2",
                    "symbol": "NVDA",
                    "side": "BUY",
                    "signal_verdict": "BULLISH",
                    "reason_for_entry": "VALID TRADE | A setup | BULLISH BIAS | HIGH CONVICTION",
                    "instrument_type": "equity",
                    "directional_exposure": "bullish",
                    "route_family": "current",
                    "route_version": "ranked_entry_v1",
                    "validation_sample_bucket": "current_route",
                },
            ]
        )

        report = svs.build_signal_execution_alignment_report(ledger)

        self.assertEqual(report["fill_count"], 2)
        self.assertEqual(report["directional_fill_count"], 2)
        self.assertEqual(report["aligned_count"], 1)
        self.assertEqual(report["mismatched_count"], 1)
        self.assertEqual(report["mismatches"][0]["symbol"], "MSFT")
        self.assertEqual(report["mismatches"][0]["expected_side"], "SELL")
        self.assertEqual(report["mismatches"][0]["actual_direction"], "BULLISH")
        self.assertEqual(report["current_route_directional_fill_count"], 1)
        self.assertEqual(report["current_route_mismatched_count"], 0)
        self.assertEqual(report["legacy_directional_fill_count"], 1)
        self.assertEqual(report["legacy_mismatched_count"], 1)
        self.assertEqual(report["retired_rule_directional_fill_count"], 1)
        self.assertEqual(report["retired_rule_mismatched_count"], 1)

    def test_signal_execution_alignment_treats_long_put_buy_as_bearish_alignment(self) -> None:
        ledger = pd.DataFrame(
            [
                {
                    "timestamp": "2026-04-20T17:00:00+00:00",
                    "event_type": "fill",
                    "trade_id": "trade-put-1",
                    "symbol": "QQQ",
                    "side": "BUY",
                    "signal_verdict": "BEARISH",
                    "instrument_type": "listed_option",
                    "option_right": "put",
                    "directional_exposure": "bearish",
                    "route_family": "current",
                    "route_version": "ranked_entry_v1",
                    "validation_sample_bucket": "current_route",
                    "reason_for_entry": "VALID TRADE | B setup | FULL BEARISH ALIGNMENT | HIGH CONVICTION",
                }
            ]
        )

        report = svs.build_signal_execution_alignment_report(ledger)

        self.assertEqual(report["directional_fill_count"], 1)
        self.assertEqual(report["aligned_count"], 1)
        self.assertEqual(report["mismatched_count"], 0)
        self.assertEqual(report["current_route_directional_fill_count"], 1)
        self.assertEqual(report["current_route_mismatched_count"], 0)

    def test_signal_execution_alignment_ignores_rows_without_directional_verdict(self) -> None:
        ledger = pd.DataFrame(
            [
                {
                    "timestamp": "2026-04-20T17:00:00+00:00",
                    "event_type": "fill",
                    "trade_id": "trade-flat-1",
                    "symbol": "SPY",
                    "side": "BUY",
                    "signal_verdict": "",
                    "instrument_type": "equity",
                    "directional_exposure": "bullish",
                    "validation_sample_bucket": "legacy",
                    "reason_for_entry": "PASS | Avoid | NO ALIGNMENT | LOW CONVICTION",
                }
            ]
        )

        report = svs.build_signal_execution_alignment_report(ledger)

        self.assertEqual(report["fill_count"], 1)
        self.assertEqual(report["directional_fill_count"], 0)
        self.assertEqual(report["mismatched_count"], 0)

    def test_current_route_mask_requires_explicit_route_metadata(self) -> None:
        ledger = pd.DataFrame(
            [
                {
                    "trade_id": "legacy-1",
                    "event_type": "fill",
                    "reason_for_entry": "VALID TRADE | A setup | BULLISH BIAS | HIGH CONVICTION",
                },
                {
                    "trade_id": "current-1",
                    "event_type": "fill",
                    "reason_for_entry": "PASS | Avoid | MIXED | LOW CONVICTION",
                    "route_family": "current",
                    "route_version": "ranked_entry_v1",
                    "validation_sample_bucket": "current_route",
                },
            ]
        )

        mask = svs._current_route_mask(ledger)

        self.assertEqual(mask.tolist(), [False, True])

    def test_build_mark_to_market_report_uses_snapshot_curve(self) -> None:
        snapshots = pd.DataFrame(
            [
                {
                    "snapshot_at": "2026-04-20T17:00:00+00:00",
                    "cycle_at": "2026-04-20T17:00:00+00:00",
                    "snapshot_at_ts": pd.Timestamp("2026-04-20T17:00:00+00:00"),
                    "equity": 100000.0,
                    "cash_estimate": 100000.0,
                    "realized_pnl": 0.0,
                    "unrealized_pnl": 0.0,
                    "gross_exposure": 0.0,
                },
                {
                    "snapshot_at": "2026-04-20T17:05:00+00:00",
                    "cycle_at": "2026-04-20T17:05:00+00:00",
                    "snapshot_at_ts": pd.Timestamp("2026-04-20T17:05:00+00:00"),
                    "equity": 120000.0,
                    "cash_estimate": 90000.0,
                    "realized_pnl": 5000.0,
                    "unrealized_pnl": 15000.0,
                    "gross_exposure": 30000.0,
                },
                {
                    "snapshot_at": "2026-04-20T17:10:00+00:00",
                    "cycle_at": "2026-04-20T17:10:00+00:00",
                    "snapshot_at_ts": pd.Timestamp("2026-04-20T17:10:00+00:00"),
                    "equity": 90000.0,
                    "cash_estimate": 70000.0,
                    "realized_pnl": -5000.0,
                    "unrealized_pnl": -5000.0,
                    "gross_exposure": 20000.0,
                },
            ]
        )

        report = svs.build_mark_to_market_report(snapshots, starting_capital=100000.0)

        self.assertEqual(report["snapshot_count"], 3)
        self.assertEqual(report["latest_equity"], 90000.0)
        self.assertEqual(report["gross_exposure_peak"], 30000.0)
        self.assertEqual(report["max_drawdown_pct"], 25.0)
        self.assertEqual(report["peak_equity"], 120000.0)
        self.assertEqual(report["trough_equity"], 90000.0)

    def test_build_metrics_source_summary_requires_complete_consistent_snapshot_window(self) -> None:
        ledger = pd.DataFrame(
            [
                {"timestamp": "2026-04-20T17:00:00+00:00"},
                {"timestamp": "2026-04-20T17:10:00+00:00"},
            ]
        )
        decision = svs._build_metrics_source_summary(
            ledger,
            pd.DataFrame(
                [
                    {
                        "snapshot_at": "2026-04-20T17:05:00+00:00",
                        "snapshot_at_ts": pd.Timestamp("2026-04-20T17:05:00+00:00"),
                        "effective_ts": pd.Timestamp("2026-04-20T17:05:00+00:00"),
                        "equity": 100500.0,
                        "cash_estimate": 98000.0,
                        "gross_exposure": 2500.0,
                    }
                ]
            ),
            ledger_metrics={"ending_equity": 100000.0, "gross_exposure_peak": 2000.0},
            mark_to_market_report={
                "snapshot_count": 1,
                "coverage_start": "2026-04-20T17:05:00+00:00",
                "coverage_end": "2026-04-20T17:05:00+00:00",
                "latest_equity": 100500.0,
                "gross_exposure_peak": 2500.0,
            },
            starting_capital=100000.0,
        )

        self.assertEqual(decision["metrics_source"], "event_ledger")
        self.assertEqual(decision["mark_to_market_coverage_status"], "partial_window")
        self.assertEqual(decision["ledger_snapshot_consistency"], "unavailable")

    def test_build_metrics_source_summary_flags_inconsistent_snapshot_curve(self) -> None:
        ledger = pd.DataFrame(
            [
                {"timestamp": "2026-04-20T17:00:00+00:00"},
                {"timestamp": "2026-04-20T17:10:00+00:00"},
            ]
        )
        decision = svs._build_metrics_source_summary(
            ledger,
            pd.DataFrame(
                [
                    {
                        "snapshot_at": "2026-04-20T17:00:00+00:00",
                        "snapshot_at_ts": pd.Timestamp("2026-04-20T17:00:00+00:00"),
                        "effective_ts": pd.Timestamp("2026-04-20T17:00:00+00:00"),
                        "equity": 100000.0,
                        "cash_estimate": 100000.0,
                        "gross_exposure": 0.0,
                    },
                    {
                        "snapshot_at": "2026-04-20T17:10:00+00:00",
                        "snapshot_at_ts": pd.Timestamp("2026-04-20T17:10:00+00:00"),
                        "effective_ts": pd.Timestamp("2026-04-20T17:10:00+00:00"),
                        "equity": 90000.0,
                        "cash_estimate": 85000.0,
                        "gross_exposure": 40000.0,
                    },
                ]
            ),
            ledger_metrics={"ending_equity": 100000.0, "gross_exposure_peak": 5000.0},
            mark_to_market_report={
                "snapshot_count": 2,
                "coverage_start": "2026-04-20T17:00:00+00:00",
                "coverage_end": "2026-04-20T17:10:00+00:00",
                "latest_equity": 90000.0,
                "gross_exposure_peak": 40000.0,
            },
            starting_capital=100000.0,
        )

        self.assertEqual(decision["metrics_source"], "event_ledger")
        self.assertEqual(decision["mark_to_market_coverage_status"], "complete")
        self.assertEqual(decision["ledger_snapshot_consistency"], "inconsistent")

    def test_build_metrics_source_summary_accepts_complete_consistent_snapshot_curve(self) -> None:
        ledger = pd.DataFrame(
            [
                {"timestamp": "2026-04-20T17:00:00+00:00"},
                {"timestamp": "2026-04-20T17:10:00+00:00"},
            ]
        )
        decision = svs._build_metrics_source_summary(
            ledger,
            pd.DataFrame(
                [
                    {
                        "snapshot_at": "2026-04-20T17:00:00+00:00",
                        "snapshot_at_ts": pd.Timestamp("2026-04-20T17:00:00+00:00"),
                        "effective_ts": pd.Timestamp("2026-04-20T17:00:00+00:00"),
                        "equity": 100000.0,
                        "cash_estimate": 100000.0,
                        "gross_exposure": 0.0,
                    },
                    {
                        "snapshot_at": "2026-04-20T17:10:00+00:00",
                        "snapshot_at_ts": pd.Timestamp("2026-04-20T17:10:00+00:00"),
                        "effective_ts": pd.Timestamp("2026-04-20T17:10:00+00:00"),
                        "equity": 100500.0,
                        "cash_estimate": 98500.0,
                        "gross_exposure": 5100.0,
                    },
                ]
            ),
            ledger_metrics={"ending_equity": 100000.0, "gross_exposure_peak": 5000.0},
            mark_to_market_report={
                "snapshot_count": 2,
                "coverage_start": "2026-04-20T17:00:00+00:00",
                "coverage_end": "2026-04-20T17:10:00+00:00",
                "latest_equity": 100500.0,
                "gross_exposure_peak": 5100.0,
            },
            starting_capital=100000.0,
        )

        self.assertEqual(decision["metrics_source"], "mark_to_market")
        self.assertEqual(decision["mark_to_market_coverage_status"], "complete")
        self.assertEqual(decision["ledger_snapshot_consistency"], "consistent")

    def test_current_route_execution_realism_marks_sample_sufficient_at_threshold(self) -> None:
        rows = []
        base_timestamp = pd.Timestamp("2026-04-20T17:00:00+00:00")
        for index in range(10):
            fill_timestamp = (base_timestamp + pd.Timedelta(minutes=index * 2)).isoformat()
            rows.append(
                {
                    "timestamp": fill_timestamp,
                    "event_type": "fill",
                    "trade_id": f"trade-{index}",
                    "symbol": "SPY",
                    "side": "BUY",
                    "signal_timestamp": fill_timestamp,
                    "order_timestamp": fill_timestamp,
                    "fill_timestamp": fill_timestamp,
                    "fill_price": 100.0 + index,
                    "quantity": 1.0,
                    "gross_exposure": 100.0 + index,
                    "realized_pnl": 0.0,
                    "fees": 0.0,
                    "slippage": 0.0,
                    "margin_interest": 0.0,
                    "equity_after_fill": 100000.0 + index,
                    "reason_for_entry": "VALID TRADE | A | FULL BULLISH ALIGNMENT | HIGH CONVICTION",
                    "reason_for_exit": "",
                    "instrument_type": "equity",
                    "interval": "5m",
                    "signal_verdict": "BULLISH",
                    "order_type": "market",
                    "status": "OPEN",
                    "route_family": "current",
                    "route_version": "ranked_entry_v1",
                    "automation_entry_reason": "ranked_candidate",
                    "thesis_direction": "BULLISH",
                    "directional_exposure": "bullish",
                    "validation_sample_bucket": "current_route",
                }
            )
            if index < 5:
                close_timestamp = (base_timestamp + pd.Timedelta(minutes=index * 2 + 1)).isoformat()
                rows.append(
                    {
                        "timestamp": close_timestamp,
                        "event_type": "close",
                        "trade_id": f"trade-{index}",
                        "symbol": "SPY",
                        "side": "SELL",
                        "signal_timestamp": fill_timestamp,
                        "order_timestamp": fill_timestamp,
                        "fill_timestamp": close_timestamp,
                        "fill_price": 101.0 + index,
                        "quantity": 1.0,
                        "gross_exposure": 0.0,
                        "realized_pnl": 10.0,
                        "fees": 0.0,
                        "slippage": 0.0,
                        "margin_interest": 0.0,
                        "equity_after_fill": 100010.0 + index,
                        "reason_for_entry": "VALID TRADE | A | FULL BULLISH ALIGNMENT | HIGH CONVICTION",
                        "reason_for_exit": "Profit exit",
                        "instrument_type": "equity",
                        "interval": "5m",
                        "signal_verdict": "BULLISH",
                        "order_type": "market",
                        "status": "CLOSED",
                        "route_family": "current",
                        "route_version": "ranked_entry_v1",
                        "automation_entry_reason": "ranked_candidate",
                        "thesis_direction": "BULLISH",
                        "directional_exposure": "bullish",
                        "validation_sample_bucket": "current_route",
                    }
                )
        ledger = pd.DataFrame(rows)

        with (
            patch.object(svs, "build_next_bar_replay_report", return_value={"items": []}),
            patch.object(svs, "build_stress_matrix_results", return_value=[]),
        ):
            report = svs.build_current_route_execution_realism_report(
                ledger,
                starting_capital=100000.0,
                current_settings={},
            )

        self.assertEqual(report["trade_count"], 15)
        self.assertEqual(report["closed_trade_count"], 5)
        self.assertEqual(report["signal_execution_alignment"]["directional_fill_count"], 10)
        self.assertEqual(report["sample_status"], "sufficient")
        self.assertEqual(report["directional_fill_threshold"], 10)
        self.assertEqual(report["closed_trade_threshold"], 5)

    def test_build_next_bar_replay_report_replays_fills_against_next_open(self) -> None:
        ledger = pd.DataFrame(
            [
                {
                    "event_type": "fill",
                    "trade_id": "trade-msft",
                    "symbol": "MSFT",
                    "interval": "5m",
                    "signal_timestamp": "2026-04-20T17:00:00+00:00",
                    "fill_price": 100.0,
                    "quantity": 10,
                    "instrument_type": "equity",
                },
                {
                    "event_type": "fill",
                    "trade_id": "trade-amd",
                    "symbol": "AMD",
                    "interval": "5m",
                    "signal_timestamp": "2026-04-20T17:00:00+00:00",
                    "fill_price": 50.0,
                    "quantity": 5,
                    "instrument_type": "equity",
                },
            ]
        )

        histories = {
            "MSFT": pd.DataFrame(
                {"Open": [100.5, 101.0], "Close": [100.5, 101.0]},
                index=pd.to_datetime(["2026-04-20T17:00:00+00:00", "2026-04-20T17:05:00+00:00"], utc=True),
            ),
            "AMD": pd.DataFrame(
                {"Open": [49.5, 49.0], "Close": [49.5, 49.0]},
                index=pd.to_datetime(["2026-04-20T17:00:00+00:00", "2026-04-20T17:05:00+00:00"], utc=True),
            ),
        }

        with patch.object(svs, "_download_history", side_effect=lambda symbol, *, period, interval: histories.get(symbol, pd.DataFrame())):
            report = svs.build_next_bar_replay_report(ledger)

        self.assertEqual(report["trade_count"], 2)
        self.assertEqual(report["replayed_count"], 2)
        self.assertEqual(report["average_entry_penalty_dollars"], 2.5)
        self.assertEqual(report["worse_fill_rate"], 0.5)
        self.assertEqual(report["items"][0]["trade_id"], "trade-msft")
        self.assertEqual(report["items"][0]["entry_penalty_dollars"], 10.0)
        self.assertTrue(report["items"][0]["worse_fill"])
        self.assertEqual(report["items"][1]["trade_id"], "trade-amd")
        self.assertEqual(report["items"][1]["entry_penalty_dollars"], -5.0)
        self.assertFalse(report["items"][1]["worse_fill"])

    def test_build_benchmark_report_compares_strategy_to_benchmarks_and_basket(self) -> None:
        ledger = pd.DataFrame(
            [
                {"timestamp": "2026-04-20T17:00:00+00:00", "symbol": "MSFT"},
                {"timestamp": "2026-04-20T17:10:00+00:00", "symbol": "AMD"},
            ]
        )
        snapshots = pd.DataFrame(
            [
                {"effective_ts": pd.Timestamp("2026-04-20T17:00:00+00:00")},
                {"effective_ts": pd.Timestamp("2026-04-20T17:10:00+00:00")},
            ]
        )

        histories = {
            "SPY": pd.DataFrame(
                {"Close": [100.0, 102.0]},
                index=pd.to_datetime(["2026-04-20T17:00:00+00:00", "2026-04-20T17:10:00+00:00"], utc=True),
            ),
            "QQQ": pd.DataFrame(
                {"Close": [200.0, 198.0]},
                index=pd.to_datetime(["2026-04-20T17:00:00+00:00", "2026-04-20T17:10:00+00:00"], utc=True),
            ),
            "AMD": pd.DataFrame(
                {"Close": [50.0, 55.0]},
                index=pd.to_datetime(["2026-04-20T17:00:00+00:00", "2026-04-20T17:10:00+00:00"], utc=True),
            ),
            "MSFT": pd.DataFrame(
                {"Close": [100.0, 95.0]},
                index=pd.to_datetime(["2026-04-20T17:00:00+00:00", "2026-04-20T17:10:00+00:00"], utc=True),
            ),
        }

        with patch.object(svs, "_download_history", side_effect=lambda symbol, *, period, interval: histories.get(symbol, pd.DataFrame())):
            report = svs.build_benchmark_report(
                ledger,
                snapshots,
                starting_capital=100000.0,
                strategy_ending_equity=101000.0,
            )

        self.assertEqual(report["benchmark_interval"], "5m")
        self.assertEqual(report["strategy_return_pct"], 1.0)
        self.assertEqual(len(report["benchmarks"]), 3)
        self.assertEqual(report["benchmarks"][0]["key"], "spy")
        self.assertEqual(report["benchmarks"][0]["return_pct"], 2.0)
        self.assertEqual(report["benchmarks"][1]["key"], "qqq")
        self.assertEqual(report["benchmarks"][1]["return_pct"], -1.0)
        self.assertEqual(report["benchmarks"][2]["key"], "equal_weight_traded_basket")
        self.assertEqual(report["benchmarks"][2]["return_pct"], 2.5)
        self.assertEqual(report["benchmarks"][2]["component_count"], 2)
        self.assertEqual(report["best_benchmark"]["key"], "equal_weight_traded_basket")
        self.assertEqual(report["best_benchmark"]["ending_equity"], 102500.0)

    def test_build_current_route_benchmark_report_filters_retired_route_rows(self) -> None:
        ledger = pd.DataFrame(
            [
                {
                    "timestamp": "2026-04-20T17:00:00+00:00",
                    "event_type": "close",
                    "symbol": "MSFT",
                    "realized_pnl": -50.0,
                    "reason_for_entry": "PASS | Avoid | BEARISH BIAS | LOW CONVICTION",
                    "validation_sample_bucket": "legacy",
                },
                {
                    "timestamp": "2026-04-20T17:10:00+00:00",
                    "event_type": "close",
                    "symbol": "AMD",
                    "realized_pnl": 200.0,
                    "reason_for_entry": "VALID TRADE | A | ALIGNED | HIGH",
                    "route_family": "current",
                    "route_version": "ranked_entry_v1",
                    "validation_sample_bucket": "current_route",
                },
            ]
        )
        snapshots = pd.DataFrame(
            [
                {"effective_ts": pd.Timestamp("2026-04-20T17:00:00+00:00")},
                {"effective_ts": pd.Timestamp("2026-04-20T17:10:00+00:00")},
            ]
        )
        histories = {
            "SPY": pd.DataFrame(
                {"Close": [100.0, 101.0]},
                index=pd.to_datetime(["2026-04-20T17:00:00+00:00", "2026-04-20T17:10:00+00:00"], utc=True),
            ),
            "QQQ": pd.DataFrame(
                {"Close": [200.0, 201.0]},
                index=pd.to_datetime(["2026-04-20T17:00:00+00:00", "2026-04-20T17:10:00+00:00"], utc=True),
            ),
            "AMD": pd.DataFrame(
                {"Close": [50.0, 55.0]},
                index=pd.to_datetime(["2026-04-20T17:00:00+00:00", "2026-04-20T17:10:00+00:00"], utc=True),
            ),
        }

        with patch.object(svs, "_download_history", side_effect=lambda symbol, *, period, interval: histories.get(symbol, pd.DataFrame())):
            report = svs.build_current_route_benchmark_report(
                ledger,
                snapshots,
                starting_capital=100000.0,
            )

        self.assertEqual(report["trade_count"], 1)
        self.assertEqual(report["closed_trade_count"], 1)
        self.assertEqual(report["strategy_return_pct"], 0.2)
        self.assertEqual(report["best_benchmark"]["key"], "equal_weight_traded_basket")
        self.assertEqual(report["best_benchmark"]["component_count"], 1)

    def test_build_walk_forward_report_rolls_train_and_test_months(self) -> None:
        ledger = pd.DataFrame(
            [
                {"event_type": "close", "timestamp": "2026-01-15T17:00:00+00:00", "realized_pnl": 100.0},
                {"event_type": "close", "timestamp": "2026-02-15T17:00:00+00:00", "realized_pnl": -50.0},
                {"event_type": "close", "timestamp": "2026-03-15T17:00:00+00:00", "realized_pnl": 80.0},
                {"event_type": "close", "timestamp": "2026-04-15T17:00:00+00:00", "realized_pnl": 20.0},
                {"event_type": "close", "timestamp": "2026-05-15T17:00:00+00:00", "realized_pnl": -10.0},
                {"event_type": "close", "timestamp": "2026-06-15T17:00:00+00:00", "realized_pnl": 60.0},
                {"event_type": "close", "timestamp": "2026-07-15T17:00:00+00:00", "realized_pnl": 40.0},
                {"event_type": "close", "timestamp": "2026-08-15T17:00:00+00:00", "realized_pnl": -20.0},
            ]
        )

        report = svs.build_walk_forward_report(
            ledger,
            starting_capital=100000.0,
        )

        self.assertEqual(report["status"], "ok")
        self.assertEqual(report["coverage_months_available"], 8)
        self.assertEqual(report["window_count"], 2)
        self.assertEqual(report["windows"][0]["training_metrics"]["trade_count"], 6)
        self.assertEqual(report["windows"][0]["test_metrics"]["trade_count"], 1)
        self.assertEqual(report["windows"][1]["training_metrics"]["trade_count"], 6)
        self.assertEqual(report["stitched_test_metrics"]["trade_count"], 2)

    def test_build_kill_switch_report_marks_missing_live_stops_as_partial(self) -> None:
        baseline = {
            "starting_capital": 100000.0,
            "current_settings": {
                "risk_percent": 0.5,
                "max_open_positions": 5,
                "max_leverage": {
                    "gross_cap_multiple": 0.5,
                    "max_total_open_notional": 50000.0,
                    "max_notional_per_trade": 25000.0,
                },
            },
        }
        metrics = {
            "ending_equity": 100061.4869,
            "gross_exposure_peak": 24999.9209,
            "daily_loss_worst": 61.4869,
            "weekly_loss_worst": 61.4869,
        }

        report = svs.build_kill_switch_report(
            baseline=baseline,
            metrics=metrics,
        )

        self.assertEqual(report["status"], "partial")
        self.assertTrue(report["checks"]["risk_per_trade_within_target_range"])
        self.assertTrue(report["checks"]["gross_exposure_within_initial_cap"])
        self.assertFalse(report["checks"]["daily_stop_configured"])
        self.assertFalse(report["checks"]["stop_bot_threshold_configured"])
        self.assertEqual(report["recommended_thresholds"]["daily_stop_loss_dollars"], 2000.0)

    def test_build_test_matrix_includes_correlation_cap_scenario(self) -> None:
        matrix = svs.build_test_matrix(
            {
                "risk_percent": 0.25,
                "max_leverage": {"gross_cap_multiple": 0.5},
            }
        )

        by_key = {item["key"]: item for item in matrix}
        self.assertIn("L", by_key)
        self.assertEqual(by_key["L"]["label"], "25% proxy correlation bucket cap")
        self.assertEqual(by_key["L"]["overrides"]["correlation_bucket_cap_pct_equity"], 25.0)
        self.assertEqual(by_key["M"]["overrides"]["gross_exposure_cap"], 1.5)
        self.assertEqual(by_key["N"]["overrides"]["correlation_bucket_cap_pct_equity"], 35.0)
        self.assertTrue(by_key["O"]["overrides"]["winner_only_pyramiding"])
        self.assertEqual(by_key["P"]["overrides"]["min_edge_to_cost_ratio"], 2.5)

    def test_simulate_variant_applies_correlation_bucket_cap_to_overlapping_trades(self) -> None:
        closes = pd.DataFrame(
            [
                {
                    "symbol": "AAPL",
                    "position_cost": 20000.0,
                    "realized_pnl": 1000.0,
                    "max_risk_dollars": 100.0,
                    "slippage": 1.0,
                    "signal_timestamp": "2026-04-20T14:00:00+00:00",
                    "timestamp": "2026-04-20T15:00:00+00:00",
                },
                {
                    "symbol": "MSFT",
                    "position_cost": 20000.0,
                    "realized_pnl": 1000.0,
                    "max_risk_dollars": 100.0,
                    "slippage": 1.0,
                    "signal_timestamp": "2026-04-20T14:30:00+00:00",
                    "timestamp": "2026-04-20T14:45:00+00:00",
                },
                {
                    "symbol": "SPY",
                    "position_cost": 20000.0,
                    "realized_pnl": 1000.0,
                    "max_risk_dollars": 100.0,
                    "slippage": 1.0,
                    "signal_timestamp": "2026-04-20T14:35:00+00:00",
                    "timestamp": "2026-04-20T15:05:00+00:00",
                },
            ]
        )

        uncapped = svs._simulate_variant(closes, starting_capital=100000.0, overrides={})
        capped = svs._simulate_variant(
            closes,
            starting_capital=100000.0,
            overrides={"correlation_bucket_cap_pct_equity": 25.0},
        )

        self.assertEqual(uncapped["closed_trade_count"], 3)
        self.assertEqual(capped["closed_trade_count"], 3)
        self.assertGreater(uncapped["ending_equity"], capped["ending_equity"])
        self.assertEqual(round(capped["ending_equity"], 2), 102247.75)

    def test_evaluate_ranked_entry_rollout_acceptance_rejects_drawdown_breach(self) -> None:
        summary = svs.evaluate_ranked_entry_rollout_acceptance(
            [
                {"key": "A", "metrics": {"ending_equity": 102000.0, "average_trade_profit": 120.0, "max_drawdown_pct": 10.0, "gross_exposure_peak": 90000.0}},
                {"key": "M", "metrics": {"ending_equity": 103000.0, "average_trade_profit": 140.0, "max_drawdown_pct": 12.0, "gross_exposure_peak": 140000.0}},
            ],
            starting_capital=100000.0,
        )

        self.assertFalse(summary["accepted"])
        self.assertEqual(summary["status"], "rejected")
        self.assertIn("drawdown", summary["basis"].lower())

    def test_evaluate_ranked_entry_rollout_acceptance_accepts_improved_profile_inside_limits(self) -> None:
        summary = svs.evaluate_ranked_entry_rollout_acceptance(
            [
                {"key": "A", "metrics": {"ending_equity": 102000.0, "average_trade_profit": 120.0, "max_drawdown_pct": 10.0, "gross_exposure_peak": 90000.0}},
                {"key": "M", "metrics": {"ending_equity": 103500.0, "average_trade_profit": 135.0, "max_drawdown_pct": 11.0, "gross_exposure_peak": 145000.0}},
            ],
            starting_capital=100000.0,
        )

        self.assertTrue(summary["accepted"])
        self.assertEqual(summary["status"], "accepted")

    def test_build_intraday_prediction_validation_report_accepts_full_hybrid_stack(self) -> None:
        rows = []
        for index in range(24):
            forecast_group_id = f"full-group-{index}"
            rows.append(
                {
                    "ticker": "NVDA",
                    "interval": "5m",
                    "forecast_at": f"2026-04-01T14:{index:02d}:00+00:00",
                    "forecast_group_id": forecast_group_id,
                    "prediction_stack_version": svs.sdm.INTRADAY_PREDICTION_STACK_VERSION,
                    "prediction_configuration": "proxy_baseline",
                    "probability_up": 0.58,
                    "actual_target_up": 1.0,
                    "actual_return": 0.004,
                    "market_regime": "trend",
                    "session_label": "morning",
                    "event_window_label": "quiet_window",
                    "volatility_regime": "normal",
                    "market_state_source": "yfinance_proxy",
                    "relative_strength_source": "yfinance_proxy",
                    "options_flow_source": "yfinance_proxy",
                    "event_revision_source": "yfinance_proxy",
                    "degraded_prediction": True,
                    "market_state_probability_shift": 0.002,
                    "relative_strength_probability_shift": 0.001,
                    "options_flow_probability_shift": 0.0,
                    "event_revision_probability_shift": 0.0,
                }
            )
            rows.append(
                {
                    "ticker": "NVDA",
                    "interval": "5m",
                    "forecast_at": f"2026-04-02T14:{index:02d}:00+00:00",
                    "forecast_group_id": forecast_group_id,
                    "prediction_stack_version": svs.sdm.INTRADAY_PREDICTION_STACK_VERSION,
                    "prediction_configuration": "full_hybrid",
                    "probability_up": 0.66,
                    "actual_target_up": 1.0,
                    "actual_return": 0.009,
                    "market_regime": "trend",
                    "session_label": "morning",
                    "event_window_label": "quiet_window",
                    "volatility_regime": "normal",
                    "market_state_source": "alpaca_market_state",
                    "relative_strength_source": "alpaca_relative_strength",
                    "options_flow_source": "polygon_options_chain",
                    "event_revision_source": "polygon_reference",
                    "degraded_prediction": False,
                    "market_state_probability_shift": 0.018,
                    "relative_strength_probability_shift": 0.014,
                    "options_flow_probability_shift": 0.011,
                    "event_revision_probability_shift": 0.009,
                }
            )
        for index in range(24):
            forecast_group_id = f"stock-group-{index}"
            rows.append(
                {
                    "ticker": "NVDA",
                    "interval": "5m",
                    "forecast_at": f"2026-04-03T14:{index:02d}:00+00:00",
                    "forecast_group_id": forecast_group_id,
                    "prediction_stack_version": svs.sdm.INTRADAY_PREDICTION_STACK_VERSION,
                    "prediction_configuration": "proxy_baseline",
                    "probability_up": 0.57,
                    "actual_target_up": 1.0,
                    "actual_return": 0.0035,
                    "market_regime": "trend",
                    "session_label": "morning",
                    "event_window_label": "quiet_window",
                    "volatility_regime": "normal",
                    "market_state_source": "yfinance_proxy",
                    "relative_strength_source": "yfinance_proxy",
                    "options_flow_source": "fallback_options_flow",
                    "event_revision_source": "polygon_reference",
                    "degraded_prediction": True,
                    "market_state_probability_shift": 0.001,
                    "relative_strength_probability_shift": 0.001,
                    "options_flow_probability_shift": 0.0,
                    "event_revision_probability_shift": 0.0,
                }
            )
            rows.append(
                {
                    "ticker": "NVDA",
                    "interval": "5m",
                    "forecast_at": f"2026-04-03T15:{index:02d}:00+00:00",
                    "forecast_group_id": forecast_group_id,
                    "prediction_stack_version": svs.sdm.INTRADAY_PREDICTION_STACK_VERSION,
                    "prediction_configuration": "hybrid_stock_only",
                    "probability_up": 0.63,
                    "actual_target_up": 1.0,
                    "actual_return": 0.0075,
                    "market_regime": "trend",
                    "session_label": "morning",
                    "event_window_label": "quiet_window",
                    "volatility_regime": "normal",
                    "market_state_source": "alpaca_market_state",
                    "relative_strength_source": "alpaca_relative_strength",
                    "options_flow_source": "fallback_options_flow",
                    "event_revision_source": "polygon_reference",
                    "degraded_prediction": False,
                    "market_state_probability_shift": 0.012,
                    "relative_strength_probability_shift": 0.01,
                    "options_flow_probability_shift": 0.0,
                    "event_revision_probability_shift": 0.006,
                }
            )

        runtime_settings = type("Settings", (), {
            "market_data_adapter": "hybrid",
            "alpaca_api_key_id": "alpaca-key",
            "alpaca_api_secret_key": "alpaca-secret",
            "polygon_api_key": "polygon-key",
        })()
        with patch.object(svs.sdm, "settings", runtime_settings):
            report = svs.build_intraday_prediction_validation_report(
                pd.DataFrame(rows),
                starting_capital=100000.0,
            )

        self.assertEqual(report["status"], "pass")
        self.assertTrue(report["accepted"])
        self.assertEqual(report["baseline_key"], "proxy_baseline")
        self.assertEqual(report["candidate_key"], "full_hybrid")
        self.assertEqual(report["preferred_candidate_configuration"], "full_hybrid")
        self.assertEqual(report["active_candidate_configuration"], "full_hybrid")
        self.assertEqual(report["prediction_promotion_tier"], "full_hybrid")
        self.assertEqual(report["active_prediction_stack_version"], svs.sdm.INTRADAY_PREDICTION_STACK_VERSION)
        self.assertEqual(report["paired_group_counts"]["full_hybrid"], 24)
        self.assertEqual(report["paired_group_counts"]["hybrid_stock_only"], 24)
        self.assertIn("market_regime", report["calibration_buckets"])
        self.assertGreaterEqual(len(report["driver_ablation"]), 1)

    def test_build_intraday_prediction_validation_report_is_partial_without_enough_sample(self) -> None:
        runtime_settings = type("Settings", (), {
            "market_data_adapter": "hybrid",
            "alpaca_api_key_id": "alpaca-key",
            "alpaca_api_secret_key": "alpaca-secret",
            "polygon_api_key": "polygon-key",
        })()
        with patch.object(svs.sdm, "settings", runtime_settings):
            report = svs.build_intraday_prediction_validation_report(
                pd.DataFrame(
                    [
                        {
                            "ticker": "SPY",
                            "interval": "5m",
                            "forecast_at": "2026-04-01T14:00:00+00:00",
                            "forecast_group_id": "group-1",
                            "prediction_stack_version": svs.sdm.INTRADAY_PREDICTION_STACK_VERSION,
                            "prediction_configuration": "full_hybrid",
                            "probability_up": 0.6,
                            "actual_target_up": 1.0,
                            "actual_return": 0.004,
                            "market_state_source": "alpaca_market_state",
                            "relative_strength_source": "alpaca_relative_strength",
                            "options_flow_source": "polygon_options_chain",
                            "event_revision_source": "polygon_reference",
                            "degraded_prediction": False,
                        }
                    ]
                ),
                starting_capital=100000.0,
            )

        self.assertEqual(report["status"], "partial")
        self.assertFalse(report["accepted"])
        self.assertEqual(report["configurations"]["full_hybrid"]["sample_status"], "insufficient")
        self.assertEqual(report["paired_group_counts"]["full_hybrid"], 0)

    def test_build_intraday_prediction_validation_report_accepts_hybrid_stock_only_no_pay_tier(self) -> None:
        rows = []
        for index in range(24):
            forecast_group_id = f"group-{index}"
            rows.append(
                {
                    "ticker": "AAPL",
                    "interval": "5m",
                    "forecast_at": f"2026-04-01T14:{index:02d}:00+00:00",
                    "forecast_group_id": forecast_group_id,
                    "prediction_stack_version": svs.sdm.INTRADAY_PREDICTION_STACK_VERSION,
                    "prediction_configuration": "proxy_baseline",
                    "probability_up": 0.56,
                    "actual_target_up": 1.0,
                    "actual_return": 0.003,
                    "market_regime": "trend",
                    "session_label": "morning",
                    "event_window_label": "quiet_window",
                    "volatility_regime": "normal",
                    "market_state_source": "yfinance_proxy",
                    "relative_strength_source": "yfinance_proxy",
                    "options_flow_source": "fallback_options_flow",
                    "event_revision_source": "polygon_reference",
                    "degraded_prediction": True,
                    "market_state_probability_shift": 0.0,
                    "relative_strength_probability_shift": 0.0,
                    "options_flow_probability_shift": 0.0,
                    "event_revision_probability_shift": 0.0,
                }
            )
            rows.append(
                {
                    "ticker": "AAPL",
                    "interval": "5m",
                    "forecast_at": f"2026-04-01T15:{index:02d}:00+00:00",
                    "forecast_group_id": forecast_group_id,
                    "prediction_stack_version": svs.sdm.INTRADAY_PREDICTION_STACK_VERSION,
                    "prediction_configuration": "hybrid_stock_only",
                    "probability_up": 0.63,
                    "actual_target_up": 1.0,
                    "actual_return": 0.007,
                    "market_regime": "trend",
                    "session_label": "morning",
                    "event_window_label": "quiet_window",
                    "volatility_regime": "normal",
                    "market_state_source": "alpaca_market_state",
                    "relative_strength_source": "alpaca_relative_strength",
                    "options_flow_source": "fallback_options_flow",
                    "event_revision_source": "polygon_reference",
                    "degraded_prediction": False,
                    "market_state_probability_shift": 0.014,
                    "relative_strength_probability_shift": 0.011,
                    "options_flow_probability_shift": 0.0,
                    "event_revision_probability_shift": 0.006,
                }
            )

        runtime_settings = type("Settings", (), {
            "market_data_adapter": "hybrid",
            "alpaca_api_key_id": "alpaca-key",
            "alpaca_api_secret_key": "alpaca-secret",
            "polygon_api_key": "polygon-key",
        })()
        with patch.object(svs.sdm, "settings", runtime_settings):
            report = svs.build_intraday_prediction_validation_report(
                pd.DataFrame(rows),
                starting_capital=100000.0,
            )

        self.assertEqual(report["status"], "pass")
        self.assertTrue(report["accepted"])
        self.assertEqual(report["candidate_key"], "hybrid_stock_only")
        self.assertEqual(report["preferred_candidate_configuration"], "hybrid_stock_only")
        self.assertEqual(report["active_candidate_configuration"], "hybrid_stock_only")
        self.assertEqual(report["prediction_promotion_tier"], "hybrid_stock_only")
        self.assertEqual(report["paired_group_counts"]["hybrid_stock_only"], 24)
        self.assertEqual(report["paired_group_counts"]["full_hybrid"], 0)
        self.assertEqual(report["label"], "hybrid_stock_only accepted")

    def test_build_intraday_prediction_validation_report_uses_hybrid_stock_only_as_partial_active_tier(self) -> None:
        runtime_settings = type("Settings", (), {
            "market_data_adapter": "hybrid",
            "alpaca_api_key_id": "alpaca-key",
            "alpaca_api_secret_key": "alpaca-secret",
            "polygon_api_key": "polygon-key",
        })()
        rows = [
            {
                "ticker": "SPY",
                "interval": "5m",
                "forecast_at": "2026-04-01T14:00:00+00:00",
                "forecast_group_id": "group-1",
                "prediction_stack_version": svs.sdm.INTRADAY_PREDICTION_STACK_VERSION,
                "prediction_configuration": "proxy_baseline",
                "probability_up": 0.56,
                "actual_target_up": 1.0,
                "actual_return": 0.002,
                "market_state_source": "yfinance_proxy",
                "relative_strength_source": "yfinance_proxy",
                "options_flow_source": "fallback_options_flow",
                "event_revision_source": "polygon_reference",
                "degraded_prediction": True,
            },
            {
                "ticker": "SPY",
                "interval": "5m",
                "forecast_at": "2026-04-01T14:05:00+00:00",
                "forecast_group_id": "group-1",
                "prediction_stack_version": svs.sdm.INTRADAY_PREDICTION_STACK_VERSION,
                "prediction_configuration": "hybrid_stock_only",
                "probability_up": 0.6,
                "actual_target_up": 1.0,
                "actual_return": 0.003,
                "market_state_source": "alpaca_market_state",
                "relative_strength_source": "alpaca_relative_strength",
                "options_flow_source": "fallback_options_flow",
                "event_revision_source": "polygon_reference",
                "degraded_prediction": False,
            },
        ]
        with patch.object(svs.sdm, "settings", runtime_settings):
            report = svs.build_intraday_prediction_validation_report(
                pd.DataFrame(rows),
                starting_capital=100000.0,
            )

        self.assertEqual(report["status"], "partial")
        self.assertFalse(report["accepted"])
        self.assertEqual(report["candidate_key"], "hybrid_stock_only")
        self.assertEqual(report["active_candidate_configuration"], "hybrid_stock_only")
        self.assertEqual(report["prediction_promotion_tier"], "hybrid_stock_only")
        self.assertIn("full_hybrid unavailable", report["basis"])
        self.assertEqual(report["label"], "hybrid_stock_only validation collecting sample")

    def test_build_intraday_prediction_validation_report_marks_active_unresolved_rows_as_collecting(self) -> None:
        runtime_settings = type("Settings", (), {
            "market_data_adapter": "hybrid",
            "alpaca_api_key_id": "alpaca-key",
            "alpaca_api_secret_key": "alpaca-secret",
            "polygon_api_key": "polygon-key",
        })()
        rows = [
            {
                "ticker": "SPY",
                "interval": "5m",
                "forecast_at": "2026-04-01T13:55:00+00:00",
                "probability_up": 0.54,
                "actual_target_up": 1.0,
                "actual_return": 0.002,
                "market_state_source": "yfinance_proxy",
                "relative_strength_source": "yfinance_proxy",
                "options_flow_source": "yfinance_proxy",
                "event_revision_source": "yfinance_proxy",
                "degraded_prediction": True,
            },
            {
                "ticker": "SPY",
                "interval": "5m",
                "forecast_at": "2026-04-01T14:00:00+00:00",
                "forecast_group_id": "group-active-1",
                "prediction_stack_version": svs.sdm.INTRADAY_PREDICTION_STACK_VERSION,
                "prediction_configuration": "proxy_baseline",
                "probability_up": 0.56,
                "actual_target_up": None,
                "actual_return": None,
                "market_state_source": "yfinance_proxy",
                "relative_strength_source": "yfinance_proxy",
                "options_flow_source": "fallback_options_flow",
                "event_revision_source": "polygon_reference",
                "degraded_prediction": True,
            },
            {
                "ticker": "SPY",
                "interval": "5m",
                "forecast_at": "2026-04-01T14:00:00+00:00",
                "forecast_group_id": "group-active-1",
                "prediction_stack_version": svs.sdm.INTRADAY_PREDICTION_STACK_VERSION,
                "prediction_configuration": "hybrid_stock_only",
                "probability_up": 0.57,
                "actual_target_up": None,
                "actual_return": None,
                "market_state_source": "alpaca_market_state",
                "relative_strength_source": "alpaca_relative_strength",
                "options_flow_source": "fallback_options_flow",
                "event_revision_source": "polygon_reference",
                "degraded_prediction": True,
            },
        ]
        with patch.object(svs.sdm, "settings", runtime_settings):
            report = svs.build_intraday_prediction_validation_report(
                pd.DataFrame(rows),
                starting_capital=100000.0,
            )

        self.assertEqual(report["status"], "partial")
        self.assertFalse(report["accepted"])
        self.assertEqual(report["active_resolved_rows"], 0)
        self.assertEqual(report["candidate_key"], "hybrid_stock_only")
        self.assertEqual(report["active_candidate_configuration"], "hybrid_stock_only")
        self.assertIn("none are resolved yet for the active prediction-stack version", report["basis"])

    def test_build_intraday_prediction_validation_report_excludes_legacy_cross_era_rows(self) -> None:
        rows = [
            {
                "ticker": "SPY",
                "interval": "5m",
                "forecast_at": "2026-04-01T14:00:00+00:00",
                "probability_up": 0.61,
                "actual_target_up": 1.0,
                "actual_return": 0.004,
                "market_state_source": "yfinance_proxy",
                "relative_strength_source": "yfinance_proxy",
                "options_flow_source": "yfinance_proxy",
                "event_revision_source": "yfinance_proxy",
                "degraded_prediction": True,
            },
            {
                "ticker": "SPY",
                "interval": "5m",
                "forecast_at": "2026-04-02T14:00:00+00:00",
                "forecast_group_id": "group-a",
                "prediction_stack_version": svs.sdm.INTRADAY_PREDICTION_STACK_VERSION,
                "prediction_configuration": "proxy_baseline",
                "probability_up": 0.58,
                "actual_target_up": 1.0,
                "actual_return": 0.004,
            },
            {
                "ticker": "SPY",
                "interval": "5m",
                "forecast_at": "2026-04-02T14:00:00+00:00",
                "forecast_group_id": "group-a",
                "prediction_stack_version": svs.sdm.INTRADAY_PREDICTION_STACK_VERSION,
                "prediction_configuration": "full_hybrid",
                "probability_up": 0.66,
                "actual_target_up": 1.0,
                "actual_return": 0.008,
            },
        ]
        runtime_settings = type("Settings", (), {
            "market_data_adapter": "hybrid",
            "alpaca_api_key_id": "alpaca-key",
            "alpaca_api_secret_key": "alpaca-secret",
            "polygon_api_key": "polygon-key",
        })()
        with patch.object(svs.sdm, "settings", runtime_settings):
            report = svs.build_intraday_prediction_validation_report(
                pd.DataFrame(rows),
                starting_capital=100000.0,
            )

        self.assertEqual(report["active_resolved_rows"], 2)
        self.assertEqual(report["legacy_resolved_rows_excluded"], 1)
        self.assertEqual(report["paired_group_counts"]["full_hybrid"], 1)

    def test_build_intraday_prediction_validation_report_blocks_when_hybrid_runtime_credentials_missing(self) -> None:
        rows = []
        for index in range(24):
            forecast_group_id = f"group-{index}"
            rows.append(
                {
                    "ticker": "QQQ",
                    "interval": "5m",
                    "forecast_at": f"2026-04-01T15:{index:02d}:00+00:00",
                    "forecast_group_id": forecast_group_id,
                    "prediction_stack_version": svs.sdm.INTRADAY_PREDICTION_STACK_VERSION,
                    "prediction_configuration": "proxy_baseline",
                    "probability_up": 0.57,
                    "actual_target_up": 1.0,
                    "actual_return": 0.003,
                }
            )
            rows.append(
                {
                    "ticker": "QQQ",
                    "interval": "5m",
                    "forecast_at": f"2026-04-01T15:{index:02d}:00+00:00",
                    "forecast_group_id": forecast_group_id,
                    "prediction_stack_version": svs.sdm.INTRADAY_PREDICTION_STACK_VERSION,
                    "prediction_configuration": "full_hybrid",
                    "probability_up": 0.64,
                    "actual_target_up": 1.0,
                    "actual_return": 0.007,
                }
            )

        runtime_settings = type("Settings", (), {
            "market_data_adapter": "hybrid",
            "alpaca_api_key_id": "",
            "alpaca_api_secret_key": "",
            "polygon_api_key": "",
        })()
        with patch.object(svs.sdm, "settings", runtime_settings):
            report = svs.build_intraday_prediction_validation_report(
                pd.DataFrame(rows),
                starting_capital=100000.0,
            )

        self.assertEqual(report["status"], "partial")
        self.assertFalse(report["accepted"])
        self.assertFalse(report["runtime_activation"]["ready"])
        self.assertIn("POLYGON_API_KEY", report["basis"])

    def test_build_validation_tracker_classifies_statuses(self) -> None:
        ledger = pd.DataFrame(
            [
                {
                    "timestamp": "2026-04-20T17:00:00+00:00",
                    "symbol": "MSFT",
                    "side": "BUY",
                    "signal_timestamp": "2026-04-20T16:55:00+00:00",
                    "order_timestamp": "2026-04-20T16:59:00+00:00",
                    "fill_timestamp": "2026-04-20T17:00:00+00:00",
                    "fill_price": 100.0,
                    "quantity": 10.0,
                    "cash_before": 100000.0,
                    "cash_after": 99000.0,
                    "position_before": 0.0,
                    "position_after": 10.0,
                    "gross_exposure": 1000.0,
                    "net_exposure": 1000.0,
                    "realized_pnl": 0.0,
                    "unrealized_pnl": 5.0,
                    "fees": 0.0,
                    "slippage": 1.0,
                    "equity_after_fill": 100005.0,
                    "reason_for_entry": "PASS | Avoid | BEARISH BIAS | LOW CONVICTION",
                    "reason_for_exit": "",
                    "route_family": "legacy",
                    "route_version": "",
                    "automation_entry_reason": "",
                    "thesis_direction": "BEARISH",
                    "directional_exposure": "bullish",
                    "validation_sample_bucket": "legacy",
                }
            ]
        )
        baseline = {
            "version": "v0",
            "tenant_slug": "alpha-desk",
            "starting_capital": 100000.0,
            "current_settings": {
                "timeframe": "5m",
                "assets_traded": ["SPY", "QQQ"],
                "execution_intent": "broker_paper",
                "fees_assumption": "No explicit per-trade fee column is modeled in current CSV trade state.",
                "margin_assumption": "Current automation enforces notional caps but does not currently model borrowed amount or margin interest explicitly in the exported equity curve.",
                "risk_percent": 0.25,
                "max_open_positions": 1,
                "regular_hours_only": True,
                "max_leverage": {"gross_cap_multiple": 0.5},
            },
            "pass_fail_targets": {
                "min_trades": 200,
                "profit_factor_min": 1.3,
                "average_trade_profit_vs_cost_multiple": 2.0,
                "max_drawdown_pct": {"target": 20.0},
            },
        }
        summary = {
            "metrics": {
                "trade_count": 6,
                "closed_trade_count": 3,
                "profit_factor": 0.9,
                "average_trade_profit": 20.0,
                "average_trade_cost": 12.5,
                "max_drawdown_pct": 0.5,
            },
            "ledger_limits": {
                "mark_to_market_complete": True,
                "mark_to_market_snapshot_count": 10,
            },
            "validation_integrity": {
                "metrics_source": "event_ledger",
                "mark_to_market_coverage_status": "partial_window",
                "ledger_snapshot_consistency": "unavailable",
                "current_route_sample_status": "insufficient",
            },
            "signal_execution_alignment": {
                "mismatched_count": 3,
                "mismatch_rate": 1.0,
                "retired_rule_directional_fill_count": 3,
                "retired_rule_mismatched_count": 3,
            },
        }
        stress_matrix = [
            {"key": "B", "metrics": {"return_pct": 0.02}},
            {"key": "C", "metrics": {"return_pct": 0.02}},
            {"key": "D", "metrics": {"return_pct": 0.02}},
            {"key": "E", "metrics": {"return_pct": -0.01}},
            {"key": "F", "metrics": {"return_pct": -0.05}},
            {"key": "G", "metrics": {"return_pct": -0.03}},
            {"key": "J", "metrics": {"return_pct": 0.01}},
            {"key": "K", "metrics": {"return_pct": 0.01}},
            {
                "key": "A",
                "metrics": {
                    "return_pct": 0.02,
                    "ending_equity": 102000.0,
                    "average_trade_profit": 120.0,
                    "max_drawdown_pct": 10.0,
                    "gross_exposure_peak": 90000.0,
                },
            },
            {"key": "H", "metrics": {"return_pct": 0.02}},
            {"key": "I", "metrics": {"return_pct": 0.02}},
            {"key": "L", "metrics": {"return_pct": 0.015}},
            {
                "key": "M",
                "metrics": {
                    "return_pct": 0.03,
                    "ending_equity": 103500.0,
                    "average_trade_profit": 135.0,
                    "max_drawdown_pct": 11.0,
                    "gross_exposure_peak": 145000.0,
                },
            },
            {"key": "N", "metrics": {"return_pct": 0.028}},
            {"key": "O", "metrics": {"return_pct": 0.026}},
            {"key": "P", "metrics": {"return_pct": 0.024}},
        ]
        benchmark_report = {
            "strategy_return_pct": 0.06,
            "best_benchmark": {"label": "QQQ", "return_pct": 0.45},
        }
        next_bar_replay = {"trade_count": 3}
        walk_forward_report = {
            "status": "insufficient_coverage",
            "window_count": 0,
            "coverage_months_available": 1,
            "required_coverage_months": 7,
            "stitched_test_metrics": {"trade_count": 0, "profit_factor": None, "max_drawdown_pct": 0.0},
        }
        drawdown_decomposition = {"window_source": "all_closed_trades_fallback", "net_realized_pnl": 10.0, "worst_trade": {"symbol": "MSFT"}}
        broker_reconciliation = {
            "issue_counts": {},
            "matched_trade_count": 5,
            "trade_count": 5,
            "current_route_issue_count": 0,
            "current_route_reconciliation_status": "waiting",
            "current_route_orphan_order_event_count": 0,
            "legacy_orphan_order_event_count": 1,
            "reconciled_current_route_fill_trade_ids": ["trade-1"],
            "reconciled_current_route_close_trade_ids": ["trade-1"],
            "last_submitted_current_route_order_at": "2026-04-22T14:00:00+00:00",
            "last_current_route_fill_at": "2026-04-22T14:01:00+00:00",
            "last_current_route_close_at": "2026-04-22T14:05:00+00:00",
        }
        monte_carlo = {"trade_count": 3, "runs": 1000, "worst_drawdown_pct": 0.0}
        current_route_execution_realism = {
            "trade_count": 2,
            "closed_trade_count": 2,
            "sample_status": "insufficient",
            "signal_execution_alignment": {
                "fill_count": 2,
                "directional_fill_count": 2,
                "mismatched_count": 1,
                "mismatch_rate": 0.5,
            },
            "stress_matrix": [
                {"key": "E", "metrics": {"return_pct": -0.01}},
                {"key": "F", "metrics": {"return_pct": -0.02}},
                {"key": "G", "metrics": {"return_pct": -0.03}},
            ],
        }

        tracker = svs.build_validation_tracker(
            baseline=baseline,
            ledger=ledger,
            summary=summary,
            stress_matrix=stress_matrix,
            benchmark_report=benchmark_report,
            current_route_benchmark_report={
                "trade_count": 2,
                "closed_trade_count": 2,
                "strategy_return_pct": -0.02,
                "best_benchmark": {"label": "QQQ", "return_pct": 0.04},
            },
            next_bar_replay=next_bar_replay,
            walk_forward_report=walk_forward_report,
            drawdown_decomposition=drawdown_decomposition,
            broker_reconciliation=broker_reconciliation,
            monte_carlo=monte_carlo,
            current_route_execution_realism=current_route_execution_realism,
        )

        self.assertEqual(tracker["overall_status"], "blocked")
        checklist = {item["key"]: item["status"] for item in tracker["checklist"]}
        self.assertEqual(checklist["freeze_v0"], "pass")
        self.assertEqual(checklist["trade_ledger"], "pass")
        self.assertEqual(checklist["accounting"], "fail")
        self.assertEqual(checklist["execution_realism"], "fail")
        self.assertEqual(checklist["benchmarking"], "fail")
        self.assertEqual(checklist["walk_forward"], "partial")
        self.assertEqual(checklist["pass_fail_metrics"], "partial")
        self.assertEqual(checklist["paper_validation"], "partial")
        self.assertEqual(checklist["ranked_entry_rollout"], "partial")
        paper_validation = next(item for item in tracker["checklist"] if item["key"] == "paper_validation")
        self.assertEqual(paper_validation["evidence"]["current_route_reconciliation_status"], "waiting")
        self.assertEqual(paper_validation["evidence"]["current_route_orphan_order_event_count"], 0)
        self.assertEqual(paper_validation["evidence"]["legacy_orphan_order_event_count"], 1)
        version_track = {item["version"]: item["status"] for item in tracker["version_track"]}
        self.assertEqual(version_track["v5"], "pass")
        self.assertEqual(version_track["v7"], "pending")

    def test_current_route_validation_integrity_uses_route_window_not_all_history(self) -> None:
        ledger = pd.DataFrame(
            [
                {
                    "timestamp": "2026-04-20T14:00:00+00:00",
                    "event_type": "fill",
                    "trade_id": "legacy-fill",
                    "symbol": "MSFT",
                    "gross_exposure": 1500.0,
                    "equity_after_fill": 100000.0,
                    "route_family": "legacy",
                    "route_version": "",
                    "automation_entry_reason": "",
                    "thesis_direction": "BULLISH",
                    "directional_exposure": "bullish",
                    "validation_sample_bucket": "legacy",
                },
                {
                    "timestamp": "2026-04-22T14:00:00+00:00",
                    "event_type": "fill",
                    "trade_id": "current-fill",
                    "symbol": "NVDA",
                    "gross_exposure": 10000.0,
                    "equity_after_fill": 100000.0,
                    "route_family": "current",
                    "route_version": "ranked_entry_v1",
                    "automation_entry_reason": "ranked_candidate",
                    "thesis_direction": "BULLISH",
                    "directional_exposure": "bullish",
                    "validation_sample_bucket": "current_route",
                },
                {
                    "timestamp": "2026-04-22T15:00:00+00:00",
                    "event_type": "close",
                    "trade_id": "current-fill",
                    "symbol": "NVDA",
                    "realized_pnl": 500.0,
                    "gross_exposure": 0.0,
                    "equity_after_fill": 100500.0,
                    "route_family": "current",
                    "route_version": "ranked_entry_v1",
                    "automation_entry_reason": "ranked_candidate",
                    "thesis_direction": "BULLISH",
                    "directional_exposure": "bullish",
                    "validation_sample_bucket": "current_route",
                },
            ]
        )
        snapshots = pd.DataFrame(
            [
                {
                    "effective_ts": pd.Timestamp("2026-04-22T14:00:00+00:00"),
                    "snapshot_at_ts": pd.Timestamp("2026-04-22T14:00:00+00:00"),
                    "equity": 100000.0,
                    "cash_estimate": 100000.0,
                    "gross_exposure": 10000.0,
                },
                {
                    "effective_ts": pd.Timestamp("2026-04-22T15:00:00+00:00"),
                    "snapshot_at_ts": pd.Timestamp("2026-04-22T15:00:00+00:00"),
                    "equity": 100500.0,
                    "cash_estimate": 100500.0,
                    "gross_exposure": 0.0,
                },
            ]
        )

        all_history_integrity = svs._build_metrics_source_summary(
            ledger,
            snapshots,
            ledger_metrics=svs.compute_ledger_metrics(ledger, starting_capital=100000.0),
            mark_to_market_report=svs.build_mark_to_market_report(snapshots, starting_capital=100000.0),
            starting_capital=100000.0,
        )
        current_route_integrity = svs._build_current_route_validation_integrity(
            ledger,
            snapshots,
            starting_capital=100000.0,
            current_route_sample_status="insufficient",
        )

        self.assertEqual(all_history_integrity["mark_to_market_coverage_status"], "partial_window")
        self.assertEqual(current_route_integrity["mark_to_market_coverage_status"], "complete")
        self.assertEqual(current_route_integrity["ledger_snapshot_consistency"], "consistent")
        self.assertEqual(current_route_integrity["metrics_source"], "mark_to_market")
        self.assertEqual(current_route_integrity["route_window_start"], "2026-04-22T14:00:00+00:00")
        self.assertEqual(current_route_integrity["route_window_end"], "2026-04-22T15:00:00+00:00")
        self.assertEqual(current_route_integrity["route_window_snapshot_count"], 2)

    def test_build_validation_tracker_uses_current_route_integrity_for_rollout_gates(self) -> None:
        ledger = pd.DataFrame(
            [
                {
                    "timestamp": "2026-04-22T14:00:00+00:00",
                    "event_type": "fill",
                    "trade_id": "trade-1",
                    "symbol": "NVDA",
                    "side": "BUY",
                    "signal_timestamp": "2026-04-22T13:55:00+00:00",
                    "order_timestamp": "2026-04-22T13:59:00+00:00",
                    "fill_timestamp": "2026-04-22T14:00:00+00:00",
                    "fill_price": 100.0,
                    "quantity": 10.0,
                    "cash_before": 100000.0,
                    "cash_after": 99000.0,
                    "position_before": 0.0,
                    "position_after": 10.0,
                    "gross_exposure": 1000.0,
                    "net_exposure": 1000.0,
                    "realized_pnl": 0.0,
                    "unrealized_pnl": 0.0,
                    "fees": 1.0,
                    "slippage": 0.5,
                    "equity_after_fill": 100000.0,
                    "reason_for_entry": "VALID TRADE | A | BULLISH | HIGH",
                    "reason_for_exit": "",
                    "borrowed_amount": 0.0,
                    "margin_interest": 0.0,
                    "cumulative_fees": 1.0,
                    "cumulative_margin_interest": 0.0,
                    "long_market_value": 1000.0,
                    "short_market_value": 0.0,
                    "route_family": "current",
                    "route_version": "ranked_entry_v1",
                    "automation_entry_reason": "ranked_candidate",
                    "thesis_direction": "BULLISH",
                    "directional_exposure": "bullish",
                    "validation_sample_bucket": "current_route",
                }
            ]
        )
        baseline = {
            "version": "v0",
            "tenant_slug": "alpha-desk",
            "starting_capital": 100000.0,
            "current_settings": {
                "timeframe": "5m",
                "assets_traded": ["SPY", "QQQ"],
                "execution_intent": "broker_paper",
                "fees_assumption": "Per-trade fees are modeled explicitly from the trade rows and default to 0 only when the source data does not provide a fee value.",
                "margin_assumption": "Borrowed amount is tracked explicitly whenever settlement cash goes negative. Margin interest is modeled explicitly at 0.00% annualized for the current cash-funded v0 caps.",
                "risk_percent": 0.25,
                "max_open_positions": 1,
                "regular_hours_only": True,
                "max_leverage": {"gross_cap_multiple": 0.5},
            },
            "pass_fail_targets": {
                "min_trades": 1,
                "profit_factor_min": 1.3,
                "average_trade_profit_vs_cost_multiple": 2.0,
                "max_drawdown_pct": {"target": 20.0},
            },
        }
        summary = {
            "metrics": {
                "trade_count": 12,
                "closed_trade_count": 6,
                "profit_factor": 1.5,
                "average_trade_profit": 150.0,
                "average_trade_cost": 20.0,
                "max_drawdown_pct": 8.0,
                "peak_borrowed_amount": 0.0,
                "total_fees": 12.0,
                "total_margin_interest": 0.0,
            },
            "ledger_limits": {
                "mark_to_market_complete": False,
                "mark_to_market_snapshot_count": 2,
            },
            "validation_integrity": {
                "metrics_source": "event_ledger",
                "mark_to_market_coverage_status": "partial_window",
                "ledger_snapshot_consistency": "unavailable",
                "current_route_sample_status": "sufficient",
            },
            "current_route_validation_integrity": {
                "metrics_source": "mark_to_market",
                "mark_to_market_coverage_status": "complete",
                "ledger_snapshot_consistency": "consistent",
                "current_route_sample_status": "sufficient",
                "route_window_start": "2026-04-22T14:00:00+00:00",
                "route_window_end": "2026-04-22T20:00:00+00:00",
                "route_window_snapshot_count": 6,
            },
            "signal_execution_alignment": {
                "mismatched_count": 3,
                "mismatch_rate": 0.5,
                "legacy_fill_count": 6,
                "legacy_directional_fill_count": 6,
                "legacy_mismatched_count": 3,
            },
        }

        tracker = svs.build_validation_tracker(
            baseline=baseline,
            ledger=ledger,
            summary=summary,
            stress_matrix=[
                {"key": "A", "metrics": {"return_pct": 0.02}},
                {"key": "B", "metrics": {"return_pct": 0.02}},
                {"key": "C", "metrics": {"return_pct": 0.02}},
                {"key": "D", "metrics": {"return_pct": 0.02}},
                {"key": "E", "metrics": {"return_pct": -0.01}},
                {"key": "F", "metrics": {"return_pct": -0.02}},
                {"key": "G", "metrics": {"return_pct": -0.03}},
                {"key": "H", "metrics": {"return_pct": 0.02}},
                {"key": "I", "metrics": {"return_pct": 0.02}},
                {"key": "J", "metrics": {"return_pct": 0.02}},
                {"key": "K", "metrics": {"return_pct": 0.02}},
                {"key": "L", "metrics": {"return_pct": 0.02}},
            ],
            benchmark_report={"strategy_return_pct": 0.01, "best_benchmark": {"label": "QQQ", "return_pct": 0.03}},
            current_route_benchmark_report={
                "trade_count": 12,
                "closed_trade_count": 6,
                "strategy_return_pct": 0.06,
                "best_benchmark": {"label": "QQQ", "return_pct": 0.04},
            },
            next_bar_replay={"trade_count": 6},
            walk_forward_report={
                "status": "insufficient_coverage",
                "window_count": 0,
                "coverage_months_available": 1,
                "required_coverage_months": 7,
                "stitched_test_metrics": {"trade_count": 0, "profit_factor": None, "max_drawdown_pct": 0.0},
            },
            drawdown_decomposition={"window_source": "all_closed_trades_fallback", "net_realized_pnl": 120.0, "worst_trade": {"symbol": "NVDA"}},
            broker_reconciliation={"issue_counts": {}, "matched_trade_count": 6, "trade_count": 6},
            monte_carlo={"trade_count": 6, "runs": 1000, "worst_drawdown_pct": 6.0},
            current_route_execution_realism={
                "trade_count": 12,
                "closed_trade_count": 6,
                "sample_status": "sufficient",
                "signal_execution_alignment": {
                    "fill_count": 12,
                    "directional_fill_count": 10,
                    "mismatched_count": 0,
                    "mismatch_rate": 0.0,
                },
                "stress_matrix": [
                    {
                        "key": "A",
                        "metrics": {
                            "ending_equity": 102000.0,
                            "average_trade_profit": 120.0,
                            "max_drawdown_pct": 10.0,
                            "gross_exposure_peak": 90000.0,
                        },
                    },
                    {
                        "key": "M",
                        "metrics": {
                            "ending_equity": 103500.0,
                            "average_trade_profit": 135.0,
                            "max_drawdown_pct": 11.0,
                            "gross_exposure_peak": 145000.0,
                        },
                    },
                    {"key": "N", "metrics": {"ending_equity": 103000.0, "average_trade_profit": 130.0, "max_drawdown_pct": 10.5, "gross_exposure_peak": 140000.0}},
                    {"key": "O", "metrics": {"ending_equity": 102800.0, "average_trade_profit": 128.0, "max_drawdown_pct": 10.2, "gross_exposure_peak": 138000.0}},
                    {"key": "P", "metrics": {"ending_equity": 102600.0, "average_trade_profit": 126.0, "max_drawdown_pct": 10.1, "gross_exposure_peak": 135000.0}},
                ],
            },
        )

        checklist = {item["key"]: item for item in tracker["checklist"]}
        self.assertEqual(checklist["accounting"]["status"], "pass")
        self.assertEqual(checklist["execution_realism"]["status"], "pass")
        self.assertEqual(checklist["benchmarking"]["status"], "pass")
        self.assertEqual(checklist["paper_validation"]["status"], "pass")
        self.assertEqual(checklist["ranked_entry_rollout"]["status"], "pass")
        self.assertEqual(checklist["ranked_entry_rollout"]["evidence"]["audit_mark_to_market_coverage_status"], "partial_window")
        self.assertEqual(checklist["ranked_entry_rollout"]["evidence"]["mark_to_market_coverage_status"], "complete")
        self.assertEqual(checklist["ranked_entry_rollout"]["evidence"]["ledger_snapshot_consistency"], "consistent")
        version_track = {item["version"]: item["status"] for item in tracker["version_track"]}
        self.assertEqual(version_track["v7"], "pass")

    def test_build_validation_tracker_marks_execution_realism_partial_when_only_retired_mismatches_exist(self) -> None:
        ledger = pd.DataFrame(
            [
                {
                    "timestamp": "2026-04-20T17:00:00+00:00",
                    "event_type": "fill",
                    "trade_id": "trade-1",
                    "symbol": "MSFT",
                    "side": "BUY",
                    "signal_timestamp": "2026-04-20T16:55:00+00:00",
                    "order_timestamp": "2026-04-20T16:59:00+00:00",
                    "fill_timestamp": "2026-04-20T17:00:00+00:00",
                    "fill_price": 100.0,
                    "quantity": 10.0,
                    "cash_before": 100000.0,
                    "cash_after": 99000.0,
                    "position_before": 0.0,
                    "position_after": 10.0,
                    "gross_exposure": 1000.0,
                    "net_exposure": 1000.0,
                    "realized_pnl": 0.0,
                    "unrealized_pnl": 0.0,
                    "fees": 0.0,
                    "slippage": 1.0,
                    "equity_after_fill": 100000.0,
                    "reason_for_entry": "PASS | Avoid | BEARISH BIAS | LOW CONVICTION",
                    "reason_for_exit": "",
                    "route_family": "legacy",
                    "route_version": "",
                    "automation_entry_reason": "",
                    "thesis_direction": "BEARISH",
                    "directional_exposure": "bullish",
                    "validation_sample_bucket": "legacy",
                }
            ]
        )
        baseline = {
            "version": "v0",
            "tenant_slug": "alpha-desk",
            "current_settings": {
                "timeframe": "5m",
                "assets_traded": ["SPY", "QQQ"],
                "execution_intent": "broker_paper",
                "fees_assumption": "Explicit fees.",
                "margin_assumption": "Explicit margin assumptions.",
                "risk_percent": 0.25,
                "max_open_positions": 1,
                "regular_hours_only": True,
                "max_leverage": {"gross_cap_multiple": 0.5},
            },
            "pass_fail_targets": {
                "min_trades": 200,
                "profit_factor_min": 1.3,
                "average_trade_profit_vs_cost_multiple": 2.0,
                "max_drawdown_pct": {"target": 20.0},
            },
        }
        summary = {
            "metrics": {
                "trade_count": 1,
                "closed_trade_count": 0,
                "profit_factor": None,
                "average_trade_profit": 0.0,
                "average_trade_cost": 2.0,
                "max_drawdown_pct": 0.0,
            },
            "ledger_limits": {
                "mark_to_market_complete": True,
                "mark_to_market_snapshot_count": 5,
            },
            "signal_execution_alignment": {
                "mismatched_count": 1,
                "mismatch_rate": 1.0,
                "retired_rule_directional_fill_count": 1,
                "retired_rule_mismatched_count": 1,
                "current_route_directional_fill_count": 0,
                "current_route_mismatched_count": 0,
            },
        }

        tracker = svs.build_validation_tracker(
            baseline=baseline,
            ledger=ledger,
            summary=summary,
            stress_matrix=[],
            benchmark_report={"strategy_return_pct": 0.0, "best_benchmark": {"label": "SPY", "return_pct": 0.0}},
            current_route_benchmark_report={"trade_count": 0, "closed_trade_count": 0, "strategy_return_pct": 0.0, "best_benchmark": None},
            next_bar_replay={"trade_count": 1},
            walk_forward_report={"status": "insufficient_coverage", "window_count": 0, "coverage_months_available": 1, "required_coverage_months": 7, "stitched_test_metrics": {"trade_count": 0, "profit_factor": None, "max_drawdown_pct": 0.0}},
            drawdown_decomposition={"window_source": "all_closed_trades_fallback", "net_realized_pnl": 0.0, "worst_trade": {"symbol": None}},
            broker_reconciliation={"issue_counts": {}, "matched_trade_count": 0, "trade_count": 0},
            monte_carlo={"trade_count": 0, "runs": 1000, "worst_drawdown_pct": 0.0},
            current_route_execution_realism={
                "trade_count": 0,
                "closed_trade_count": 0,
                "signal_execution_alignment": {
                    "fill_count": 0,
                    "directional_fill_count": 0,
                    "mismatched_count": 0,
                    "mismatch_rate": 0.0,
                },
                "stress_matrix": [],
            },
        )

        checklist = {item["key"]: item for item in tracker["checklist"]}
        self.assertEqual(checklist["execution_realism"]["status"], "partial")
        self.assertEqual(checklist["execution_realism"]["evidence"]["retired_rule_mismatched_count"], 1)
        self.assertEqual(checklist["execution_realism"]["evidence"]["current_route_directional_fill_count"], 0)
        self.assertEqual(checklist["benchmarking"]["status"], "partial")
        self.assertEqual(checklist["benchmarking"]["evidence"]["current_route_closed_trade_count"], 0)
        self.assertEqual(checklist["pass_fail_metrics"]["status"], "partial")

    def test_build_validation_tracker_marks_accounting_pass_when_columns_are_explicit(self) -> None:
        ledger = pd.DataFrame(
            [
                {
                    "timestamp": "2026-04-20T17:00:00+00:00",
                    "event_type": "fill",
                    "trade_id": "trade-1",
                    "symbol": "MSFT",
                    "side": "BUY",
                    "signal_timestamp": "2026-04-20T16:55:00+00:00",
                    "order_timestamp": "2026-04-20T16:59:00+00:00",
                    "fill_timestamp": "2026-04-20T17:00:00+00:00",
                    "fill_price": 100.0,
                    "quantity": 10.0,
                    "cash_before": 100000.0,
                    "cash_after": 99000.0,
                    "position_before": 0.0,
                    "position_after": 10.0,
                    "gross_exposure": 1000.0,
                    "net_exposure": 1000.0,
                    "long_market_value": 1000.0,
                    "short_market_value": 0.0,
                    "borrowed_amount": 0.0,
                    "realized_pnl": 0.0,
                    "unrealized_pnl": 0.0,
                    "fees": 1.0,
                    "slippage": 1.0,
                    "margin_interest": 0.0,
                    "cumulative_fees": 1.0,
                    "cumulative_margin_interest": 0.0,
                    "equity_after_fill": 99999.0,
                    "reason_for_entry": "VALID TRADE | A | BULLISH | HIGH",
                    "reason_for_exit": "",
                    "route_family": "current",
                    "route_version": "ranked_entry_v1",
                    "automation_entry_reason": "ranked_candidate",
                    "thesis_direction": "BULLISH",
                    "directional_exposure": "bullish",
                    "validation_sample_bucket": "current_route",
                }
            ]
        )
        baseline = {
            "version": "v0",
            "tenant_slug": "alpha-desk",
            "current_settings": {
                "timeframe": "5m",
                "assets_traded": ["SPY", "QQQ"],
                "execution_intent": "broker_paper",
                "fees_assumption": "Per-trade fees are modeled explicitly from the trade rows and default to 0 only when the source data does not provide a fee value.",
                "margin_assumption": "Borrowed amount is tracked explicitly whenever settlement cash goes negative. Margin interest is modeled explicitly at 0.00% annualized for the current cash-funded v0 caps.",
                "risk_percent": 0.25,
                "max_open_positions": 1,
                "regular_hours_only": True,
                "max_leverage": {"gross_cap_multiple": 0.5},
            },
            "pass_fail_targets": {
                "min_trades": 200,
                "profit_factor_min": 1.3,
                "average_trade_profit_vs_cost_multiple": 2.0,
                "max_drawdown_pct": {"target": 20.0},
            },
        }
        summary = {
            "metrics": {
                "trade_count": 1,
                "closed_trade_count": 0,
                "profit_factor": None,
                "average_trade_profit": 0.0,
                "average_trade_cost": 2.0,
                "max_drawdown_pct": 0.0,
                "peak_borrowed_amount": 0.0,
                "total_fees": 1.0,
                "total_margin_interest": 0.0,
            },
            "ledger_limits": {
                "mark_to_market_complete": True,
                "mark_to_market_snapshot_count": 5,
            },
            "validation_integrity": {
                "metrics_source": "mark_to_market",
                "mark_to_market_coverage_status": "complete",
                "ledger_snapshot_consistency": "consistent",
                "current_route_sample_status": "insufficient",
            },
            "signal_execution_alignment": {
                "mismatched_count": 0,
                "mismatch_rate": 0.0,
            },
        }

        tracker = svs.build_validation_tracker(
            baseline=baseline,
            ledger=ledger,
            summary=summary,
            stress_matrix=[],
            benchmark_report={"strategy_return_pct": 0.0, "best_benchmark": {"label": "SPY", "return_pct": 0.0}},
            current_route_benchmark_report={"trade_count": 1, "closed_trade_count": 0, "strategy_return_pct": 0.0, "best_benchmark": None},
            next_bar_replay={"trade_count": 0},
            walk_forward_report={"status": "insufficient_coverage", "window_count": 0, "coverage_months_available": 1, "required_coverage_months": 7, "stitched_test_metrics": {"trade_count": 0, "profit_factor": None, "max_drawdown_pct": 0.0}},
            drawdown_decomposition={"window_source": "all_closed_trades_fallback", "net_realized_pnl": 0.0, "worst_trade": {"symbol": None}},
            broker_reconciliation={"issue_counts": {}, "matched_trade_count": 0, "trade_count": 0},
            monte_carlo={"trade_count": 0, "runs": 1000, "worst_drawdown_pct": 0.0},
            current_route_execution_realism={
                "trade_count": 1,
                "closed_trade_count": 0,
                "signal_execution_alignment": {
                    "fill_count": 1,
                    "directional_fill_count": 1,
                    "mismatched_count": 0,
                    "mismatch_rate": 0.0,
                },
                "stress_matrix": [],
            },
        )

        checklist = {item["key"]: item for item in tracker["checklist"]}
        self.assertEqual(checklist["accounting"]["status"], "pass")
        self.assertTrue(checklist["accounting"]["evidence"]["accounting_columns_present"])

    def test_build_drawdown_decomposition_report_attributes_losses(self) -> None:
        ledger = pd.DataFrame(
            [
                {
                    "timestamp": "2026-04-20T17:00:00+00:00",
                    "event_type": "close",
                    "trade_id": "trade-msft",
                    "symbol": "MSFT",
                    "realized_pnl": -100.0,
                    "reason_for_entry": "VALID TRADE | A | BULLISH | HIGH",
                    "reason_for_exit": "Loss exit",
                    "signal_verdict": "BULLISH",
                    "position_cost": 12000.0,
                    "signal_timestamp": "2026-04-20T16:50:00+00:00",
                },
                {
                    "timestamp": "2026-04-20T17:05:00+00:00",
                    "event_type": "close",
                    "trade_id": "trade-amd",
                    "symbol": "AMD",
                    "realized_pnl": 40.0,
                    "reason_for_entry": "VALID TRADE | B | BULLISH | MEDIUM",
                    "reason_for_exit": "Profit exit",
                    "signal_verdict": "BULLISH",
                    "position_cost": 4000.0,
                    "signal_timestamp": "2026-04-20T17:03:00+00:00",
                },
            ]
        )
        snapshots = pd.DataFrame(
            [
                {
                    "effective_ts": pd.Timestamp("2026-04-20T16:55:00+00:00"),
                    "snapshot_at_ts": pd.Timestamp("2026-04-20T16:55:00+00:00"),
                    "equity": 101000.0,
                },
                {
                    "effective_ts": pd.Timestamp("2026-04-20T17:06:00+00:00"),
                    "snapshot_at_ts": pd.Timestamp("2026-04-20T17:06:00+00:00"),
                    "equity": 99000.0,
                },
            ]
        )

        report = svs.build_drawdown_decomposition_report(ledger, snapshots, starting_capital=100000.0)

        self.assertEqual(report["window_source"], "mark_to_market_drawdown_window")
        self.assertEqual(report["close_count"], 2)
        self.assertEqual(report["net_realized_pnl"], -60.0)
        self.assertEqual(report["worst_trade"]["trade_id"], "trade-msft")
        self.assertEqual(report["worst_trade"]["proxy_correlation_bucket"], "mega_cap_tech")
        self.assertEqual(report["by_symbol"][0]["bucket"], "MSFT")
        self.assertEqual(report["by_symbol"][0]["realized_pnl"], -100.0)
        self.assertEqual(report["by_stop_behavior"][0]["bucket"], "loss_exit")
        self.assertEqual(report["by_position_size_bucket"][0]["bucket"], "10k-25k")

    def test_build_broker_reconciliation_report_flags_quantity_and_event_gaps(self) -> None:
        ledger = pd.DataFrame(
            [
                {
                    "event_type": "fill",
                    "trade_id": "trade-good",
                    "symbol": "MSFT",
                    "quantity": 10.0,
                    "fill_price": 100.0,
                },
                {
                    "event_type": "close",
                    "trade_id": "trade-good",
                    "symbol": "MSFT",
                    "quantity": 10.0,
                    "fill_price": 101.0,
                },
                {
                    "event_type": "fill",
                    "trade_id": "trade-bad",
                    "symbol": "AMD",
                    "quantity": 8.0,
                    "fill_price": 50.0,
                },
            ]
        )
        open_trades = pd.DataFrame(
            [
                {
                    "trade_id": "trade-good",
                    "ticker": "MSFT",
                    "broker_name": "alpaca_paper",
                    "broker_order_id": "broker-good",
                    "broker_filled_qty": 10.0,
                    "actual_fill_price": 100.0,
                    "suggested_contracts": 10.0,
                    "remaining_contracts": 0.0,
                    "status": "OPEN",
                }
            ]
        )
        closed_trades = pd.DataFrame(
            [
                {
                    "trade_id": "trade-good",
                    "ticker": "MSFT",
                    "broker_name": "alpaca_paper",
                    "broker_order_id": "broker-good",
                    "broker_filled_qty": 10.0,
                    "actual_fill_price": 100.0,
                    "closed_contracts": 10.0,
                    "remaining_contracts_after_close": 0.0,
                    "status": "CLOSED",
                },
                {
                    "trade_id": "trade-bad",
                    "ticker": "AMD",
                    "broker_name": "alpaca_paper",
                    "broker_order_id": "broker-bad",
                    "broker_filled_qty": 10.0,
                    "actual_fill_price": 50.0,
                    "closed_contracts": 10.0,
                    "remaining_contracts_after_close": 0.0,
                    "status": "CLOSED",
                },
            ]
        )
        order_events = pd.DataFrame(
            [
                {"trade_id": "trade-good", "event_key": "order.submitted", "status": "submitting"},
                {"trade_id": "trade-good", "event_key": "order.filled", "status": "filled"},
                {"trade_id": "trade-good", "event_key": "order.closed", "status": "closed"},
                {"trade_id": "trade-bad", "event_key": "order.submitted", "status": "submitting"},
            ]
        )

        report = svs.build_broker_reconciliation_report(
            ledger,
            open_trades=open_trades,
            closed_trades=closed_trades,
            pending_orders=pd.DataFrame(),
            order_events=order_events,
        )

        self.assertEqual(report["trade_count"], 2)
        self.assertEqual(report["matched_trade_count"], 1)
        self.assertEqual(report["issue_counts"]["missing_order_filled_event"], 1)
        self.assertEqual(report["issue_counts"]["fill_quantity_mismatch"], 1)
        self.assertEqual(report["issue_counts"]["close_quantity_mismatch"], 1)
        self.assertEqual(report["issue_counts"]["missing_ledger_close"], 1)
        mismatched = next(item for item in report["items"] if item["trade_id"] == "trade-bad")
        self.assertIn("missing_order_filled_event", mismatched["issues"])
        self.assertIn("fill_quantity_mismatch", mismatched["issues"])
        self.assertEqual(mismatched["ledger_fill_qty"], 8.0)
        self.assertEqual(mismatched["local_entry_qty"], 10.0)

    def test_build_broker_reconciliation_report_matches_current_route_by_correlation_id_and_splits_orphans(self) -> None:
        ledger = pd.DataFrame(
            [
                {
                    "event_type": "fill",
                    "trade_id": "trade-current",
                    "symbol": "NVDA",
                    "quantity": 2.0,
                    "fill_price": 100.0,
                    "route_family": "current",
                    "route_version": "ranked_entry_v1",
                    "route_correlation_id": "corr-current",
                    "validation_sample_bucket": "current_route",
                },
                {
                    "event_type": "close",
                    "trade_id": "trade-current",
                    "symbol": "NVDA",
                    "quantity": 2.0,
                    "fill_price": 101.0,
                    "route_family": "current",
                    "route_version": "ranked_entry_v1",
                    "route_correlation_id": "corr-current",
                    "validation_sample_bucket": "current_route",
                },
            ]
        )
        open_trades = pd.DataFrame(
            [
                {
                    "trade_id": "trade-current",
                    "ticker": "NVDA",
                    "broker_name": "alpaca_paper",
                    "broker_order_id": "broker-current",
                    "broker_filled_qty": 2.0,
                    "actual_fill_price": 100.0,
                    "suggested_contracts": 2.0,
                    "closed_contracts": 0.0,
                    "remaining_contracts": 0.0,
                    "route_family": "current",
                    "route_version": "ranked_entry_v1",
                    "route_correlation_id": "corr-current",
                    "validation_sample_bucket": "current_route",
                }
            ]
        )
        closed_trades = pd.DataFrame(
            [
                {
                    "trade_id": "trade-current",
                    "ticker": "NVDA",
                    "broker_name": "alpaca_paper",
                    "broker_order_id": "broker-current",
                    "broker_filled_qty": 2.0,
                    "actual_fill_price": 100.0,
                    "closed_contracts": 2.0,
                    "remaining_contracts_after_close": 0.0,
                    "route_family": "current",
                    "route_version": "ranked_entry_v1",
                    "route_correlation_id": "corr-current",
                    "validation_sample_bucket": "current_route",
                }
            ]
        )
        order_events = pd.DataFrame(
            [
                {
                    "trade_id": "",
                    "event_key": "order.submitted",
                    "status": "submitting",
                    "created_at": "2026-04-20T14:00:00+00:00",
                    "payload_json": {
                        "route_family": "current",
                        "route_version": "ranked_entry_v1",
                        "route_correlation_id": "corr-current",
                        "validation_sample_bucket": "current_route",
                    },
                },
                {
                    "trade_id": "",
                    "event_key": "order.filled",
                    "status": "filled",
                    "created_at": "2026-04-20T14:01:00+00:00",
                    "payload_json": {
                        "route_family": "current",
                        "route_version": "ranked_entry_v1",
                        "route_correlation_id": "corr-current",
                        "validation_sample_bucket": "current_route",
                    },
                },
                {
                    "trade_id": "",
                    "event_key": "order.closed",
                    "status": "closed",
                    "created_at": "2026-04-20T14:10:00+00:00",
                    "payload_json": {
                        "route_family": "current",
                        "route_version": "ranked_entry_v1",
                        "route_correlation_id": "corr-current",
                        "validation_sample_bucket": "current_route",
                    },
                },
                {
                    "trade_id": "",
                    "event_key": "order.submitted",
                    "status": "submitting",
                    "created_at": "2026-04-20T14:15:00+00:00",
                    "payload_json": {
                        "route_family": "current",
                        "route_version": "ranked_entry_v1",
                        "route_correlation_id": "corr-orphan",
                        "validation_sample_bucket": "current_route",
                    },
                },
                {
                    "trade_id": "legacy-orphan",
                    "event_key": "order.submitted",
                    "status": "submitting",
                    "created_at": "2026-04-20T14:16:00+00:00",
                    "payload_json": {
                        "route_family": "legacy",
                        "validation_sample_bucket": "legacy",
                    },
                },
            ]
        )

        report = svs.build_broker_reconciliation_report(
            ledger,
            open_trades=open_trades,
            closed_trades=closed_trades,
            pending_orders=pd.DataFrame(),
            order_events=order_events,
        )

        current_item = next(item for item in report["items"] if item["trade_id"] == "trade-current")
        self.assertIn("route_correlation_id", current_item["match_modes"])
        self.assertTrue(current_item["fill_reconciled"])
        self.assertTrue(current_item["close_reconciled"])
        self.assertIn("trade-current", report["reconciled_current_route_fill_trade_ids"])
        self.assertIn("trade-current", report["reconciled_current_route_close_trade_ids"])
        self.assertEqual(report["current_route_orphan_order_event_count"], 1)
        self.assertEqual(report["legacy_orphan_order_event_count"], 1)
        self.assertEqual(report["current_route_reconciliation_status"], "orphaned")

    def test_current_route_execution_realism_uses_reconciled_current_route_subset(self) -> None:
        ledger_rows = []
        base_timestamp = pd.Timestamp("2026-04-20T17:00:00+00:00")
        for index in range(3):
            trade_id = f"trade-{index}"
            fill_timestamp = (base_timestamp + pd.Timedelta(minutes=index * 5)).isoformat()
            close_timestamp = (base_timestamp + pd.Timedelta(minutes=index * 5 + 2)).isoformat()
            ledger_rows.append(
                {
                    "timestamp": fill_timestamp,
                    "event_type": "fill",
                    "trade_id": trade_id,
                    "symbol": "NVDA",
                    "signal_verdict": "BULLISH",
                    "directional_exposure": "bullish",
                    "gross_exposure": 1000.0,
                    "equity_after_fill": 100000.0,
                    "route_family": "current",
                    "route_version": "ranked_entry_v1",
                    "route_correlation_id": f"corr-{index}",
                    "validation_sample_bucket": "current_route",
                    "realized_pnl": 0.0,
                }
            )
            ledger_rows.append(
                {
                    "timestamp": close_timestamp,
                    "event_type": "close",
                    "trade_id": trade_id,
                    "symbol": "NVDA",
                    "signal_verdict": "BULLISH",
                    "directional_exposure": "bullish",
                    "gross_exposure": 0.0,
                    "equity_after_fill": 100100.0,
                    "route_family": "current",
                    "route_version": "ranked_entry_v1",
                    "route_correlation_id": f"corr-{index}",
                    "validation_sample_bucket": "current_route",
                    "realized_pnl": 10.0,
                }
            )
        ledger = pd.DataFrame(ledger_rows)

        report = svs.build_current_route_execution_realism_report(
            ledger,
            starting_capital=100000.0,
            current_settings={},
            broker_reconciliation={
                "reconciled_current_route_fill_trade_ids": ["trade-0"],
                "reconciled_current_route_close_trade_ids": [],
                "current_route_reconciliation_status": "issues_present",
                "current_route_orphan_order_event_count": 2,
                "legacy_orphan_order_event_count": 1,
            },
        )

        self.assertEqual(report["trade_count"], 1)
        self.assertEqual(report["closed_trade_count"], 0)
        self.assertEqual(report["signal_execution_alignment"]["directional_fill_count"], 1)
        self.assertEqual(report["sample_status"], "insufficient")
        self.assertEqual(report["current_route_reconciliation_status"], "issues_present")
        self.assertEqual(report["current_route_orphan_order_event_count"], 2)
        self.assertEqual(report["legacy_orphan_order_event_count"], 1)

    def test_export_strategy_validation_writes_full_broker_reconciliation_shape(self) -> None:
        broker_reconciliation = {
            "trade_count": 3,
            "matched_trade_count": 1,
            "issue_counts": {"orphan_order_events": 2},
            "current_route_issue_counts": {"current_route_orphan_order_events": 2},
            "current_route_issue_count": 2,
            "current_route_reconciliation_status": "orphaned",
            "current_route_orphan_order_event_count": 2,
            "legacy_orphan_order_event_count": 1,
            "reconciled_current_route_fill_trade_ids": ["trade-1"],
            "reconciled_current_route_close_trade_ids": ["trade-1"],
            "last_submitted_current_route_order_at": "2026-04-22T14:00:00+00:00",
            "last_current_route_fill_at": "2026-04-22T14:01:00+00:00",
            "last_current_route_close_at": "2026-04-22T14:05:00+00:00",
            "items": [],
        }
        baseline = {"tenant_slug": "alpha-desk", "version": "v0", "current_settings": {}}
        ledger = pd.DataFrame(columns=["trade_id", "event_type"])
        metrics = {
            "trade_count": 0,
            "closed_trade_count": 0,
            "ending_equity": 100000.0,
            "return_pct": 0.0,
            "gross_exposure_peak": 0.0,
            "max_drawdown_pct": 0.0,
        }
        validation_integrity = {
            "use_mark_to_market": False,
            "mark_to_market_coverage_status": "missing",
            "metrics_source": "event_ledger",
            "ledger_snapshot_consistency": "unavailable",
        }
        current_route_validation_integrity = {
            "status": "partial",
            "metrics_source": "event_ledger",
            "mark_to_market_coverage_status": "missing",
            "ledger_snapshot_consistency": "unavailable",
            "current_route_sample_status": "insufficient",
            "route_window_start": None,
            "route_window_end": None,
            "route_window_snapshot_count": 0,
            "route_window_metrics_source": "event_ledger",
            "route_window_mark_to_market_coverage_status": "missing",
            "route_window_ledger_snapshot_consistency": "unavailable",
        }

        with tempfile.TemporaryDirectory() as tmp_dir:
            output_dir = Path(tmp_dir)
            with ExitStack() as stack:
                stack.enter_context(patch.object(svs, "build_v0_baseline", return_value=baseline))
                stack.enter_context(patch.object(svs, "build_trade_validation_ledger", return_value=ledger))
                stack.enter_context(
                    patch.object(
                        svs,
                        "_load_tenant_row",
                        return_value={"id": "tenant-1", "slug": "alpha-desk", "name": "Alpha Desk", "metadata": {}},
                    )
                )
                stack.enter_context(
                    patch.object(
                        svs,
                        "_load_trade_frames",
                        return_value={
                            "open_trades": pd.DataFrame(),
                            "closed_trades": pd.DataFrame(),
                            "pending_orders": pd.DataFrame(),
                            "forecast_journal": pd.DataFrame(),
                            "trade_journal": pd.DataFrame(),
                        },
                    )
                )
                stack.enter_context(patch.object(svs, "_load_order_events", return_value=pd.DataFrame()))
                stack.enter_context(patch.object(svs, "_load_tenant_equity_snapshots", return_value=pd.DataFrame()))
                stack.enter_context(patch.object(svs, "build_broker_reconciliation_report", return_value=broker_reconciliation))
                stack.enter_context(patch.object(svs, "build_next_bar_replay_report", return_value={"trade_count": 0, "items": []}))
                stack.enter_context(
                    patch.object(
                        svs,
                        "build_current_route_execution_realism_report",
                        return_value={
                            "sample_status": "insufficient",
                            "signal_execution_alignment": {},
                            "stress_matrix": [],
                            "trade_count": 0,
                            "closed_trade_count": 0,
                        },
                    )
                )
                stack.enter_context(
                    patch.object(svs, "_build_current_route_validation_integrity", return_value=current_route_validation_integrity)
                )
                stack.enter_context(patch.object(svs, "build_walk_forward_report", return_value={"status": "pending", "window_count": 0}))
                stack.enter_context(patch.object(svs, "compute_ledger_metrics", return_value=dict(metrics)))
                stack.enter_context(
                    patch.object(
                        svs,
                        "build_signal_execution_alignment_report",
                        return_value={"fill_count": 0, "directional_fill_count": 0, "mismatched_count": 0},
                    )
                )
                stack.enter_context(patch.object(svs, "build_drawdown_report", return_value={"max_drawdown_pct": 0.0}))
                stack.enter_context(
                    patch.object(
                        svs,
                        "build_mark_to_market_report",
                        return_value={
                            "snapshot_count": 0,
                            "coverage_start": None,
                            "coverage_end": None,
                            "latest_equity": 100000.0,
                            "gross_exposure_peak": 0.0,
                            "max_drawdown_pct": 0.0,
                        },
                    )
                )
                stack.enter_context(patch.object(svs, "_build_metrics_source_summary", return_value=dict(validation_integrity)))
                stack.enter_context(patch.object(svs, "build_drawdown_decomposition_report", return_value={"window_source": "none"}))
                stack.enter_context(patch.object(svs, "build_benchmark_report", return_value={"strategy_return_pct": 0.0, "best_benchmark": None}))
                stack.enter_context(
                    patch.object(svs, "build_current_route_benchmark_report", return_value={"strategy_return_pct": 0.0, "best_benchmark": None})
                )
                stack.enter_context(
                    patch.object(svs, "build_kill_switch_report", return_value={"status": "pending", "summary": "pending", "detail": "pending"})
                )
                stack.enter_context(patch.object(svs, "run_trade_order_monte_carlo", return_value={"trade_count": 0}))
                stack.enter_context(patch.object(svs, "build_stress_matrix_results", return_value=[]))
                stack.enter_context(
                    patch.object(
                        svs,
                        "build_validation_tracker",
                        return_value={"checklist": [], "version_track": [], "overall_status": "in_progress"},
                    )
                )
                stack.enter_context(patch.object(svs, "_format_tracker_markdown", return_value="tracker"))
                svs.export_strategy_validation(
                    tenant_slug="alpha-desk",
                    starting_capital=100000.0,
                    known_peak_equity=1900000.0,
                    known_drawdown_peak=1800000.0,
                    known_drawdown_trough=1300000.0,
                    known_max_drawdown_pct=27.8,
                    output_dir=output_dir,
                )

            summary_payload = json.loads((output_dir / "summary.json").read_text(encoding="utf-8"))
            broker_payload = json.loads((output_dir / "broker_reconciliation.json").read_text(encoding="utf-8"))
            intraday_payload = json.loads((output_dir / "intraday_prediction_validation.json").read_text(encoding="utf-8"))

        for payload in (summary_payload["broker_reconciliation"], broker_payload):
            self.assertEqual(payload["current_route_reconciliation_status"], "orphaned")
            self.assertEqual(payload["current_route_orphan_order_event_count"], 2)
            self.assertEqual(payload["legacy_orphan_order_event_count"], 1)
            self.assertEqual(payload["reconciled_current_route_fill_trade_ids"], ["trade-1"])
            self.assertEqual(payload["reconciled_current_route_close_trade_ids"], ["trade-1"])
            self.assertEqual(payload["last_submitted_current_route_order_at"], "2026-04-22T14:00:00+00:00")
            self.assertEqual(payload["last_current_route_fill_at"], "2026-04-22T14:01:00+00:00")
            self.assertEqual(payload["last_current_route_close_at"], "2026-04-22T14:05:00+00:00")
        self.assertIn("intraday_prediction_validation", summary_payload)
        self.assertEqual(summary_payload["intraday_prediction_validation"]["status"], intraday_payload["status"])
        self.assertEqual(
            summary_payload["intraday_prediction_validation"]["active_candidate_configuration"],
            intraday_payload["active_candidate_configuration"],
        )
        self.assertEqual(
            summary_payload["intraday_prediction_validation"]["preferred_candidate_configuration"],
            intraday_payload["preferred_candidate_configuration"],
        )
        self.assertEqual(
            summary_payload["intraday_prediction_validation"]["prediction_promotion_tier"],
            intraday_payload["prediction_promotion_tier"],
        )


if __name__ == "__main__":
    unittest.main()
