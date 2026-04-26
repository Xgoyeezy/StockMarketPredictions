from __future__ import annotations

from contextlib import ExitStack
import gc
import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pandas as pd
from sqlalchemy.exc import OperationalError, TimeoutError as SQLAlchemyTimeoutError
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from backend.schemas import ChartRequest, CompareRequest
from backend.services.exceptions import ForbiddenError, UnauthorizedError, ValidationError, ValidationServiceError
from backend.services.storage_utils import read_json_file, write_json_file

_WORKSPACE_TEMP_ROOT = Path("tests") / "_tmp_runtime"


def _resolved_permissions(*, membership_role: str = "owner", platform_role: str = "admin", mode: str = "demo", scopes: tuple[str, ...] = ()) -> tuple[str, ...]:
    from backend.services.permissions import resolve_user_permissions

    return resolve_user_permissions(
        membership_role=membership_role,
        platform_role=platform_role,
        api_token_scopes=scopes,
        mode=mode,
    )


def _workspace_tempdir() -> tempfile.TemporaryDirectory[str]:
    _WORKSPACE_TEMP_ROOT.mkdir(parents=True, exist_ok=True)
    return tempfile.TemporaryDirectory(dir=_WORKSPACE_TEMP_ROOT)


class BackendBehaviorTests(unittest.TestCase):
    @classmethod
    def tearDownClass(cls) -> None:
        try:
            from backend.core.database import engine as application_engine

            application_engine.dispose()
        except Exception:
            pass
        gc.collect()

    def setUp(self) -> None:
        self._tracked_test_engines = []
        self._original_create_engine = globals()["create_engine"]

        def _tracked_create_engine(*args, **kwargs):
            engine = self._original_create_engine(*args, **kwargs)
            self._tracked_test_engines.append(engine)
            return engine

        globals()["create_engine"] = _tracked_create_engine
        self.addCleanup(self._cleanup_test_engines)
        try:
            from backend.services import frontend_service, market_service, ops_service

            frontend_service.clear_frontend_snapshot_cache()
            market_service.clear_market_response_cache()
            ops_service.reset_request_metrics()
            ops_service.reset_operation_metrics()
            ops_service.reset_route_profile_metrics()
            ops_service.reset_upstream_metrics()
        except Exception:
            pass

    def _cleanup_test_engines(self) -> None:
        globals()["create_engine"] = self._original_create_engine
        for engine in reversed(self._tracked_test_engines):
            try:
                engine.dispose()
            except Exception:
                pass
        self._tracked_test_engines.clear()
        gc.collect()

    def test_chart_payload_normalizes_duplicate_and_unsorted_candles(self) -> None:
        from backend.services import market_service

        frame = pd.DataFrame(
            {
                "Open": [12.0, 10.0, 10.2],
                "High": [13.0, 10.5, 10.1],
                "Low": [11.0, 9.5, 9.9],
                "Close": [12.5, 10.1, 10.4],
                "Volume": [150.0, 100.0, 200.0],
            },
            index=pd.to_datetime(
                [
                    "2026-01-01T09:35:00Z",
                    "2026-01-01T09:30:00Z",
                    "2026-01-01T09:30:00Z",
                ]
            ),
        )

        with (
            patch.object(
                market_service,
                "make_settings",
                return_value=SimpleNamespace(period="5d", interval="5m"),
            ),
            patch.object(market_service.sdm, "download_ohlcv", return_value=frame),
            patch.object(
                market_service.sdm,
                "analyze_ticker",
                return_value={
                    "forecast": {
                        "forecast_horizon_bars": 5,
                        "expected_price": 10.8,
                        "upper_price": 11.1,
                        "lower_price": 10.2,
                        "adjusted_expected_move": 0.04,
                        "confidence_score": 0.72,
                        "news_sentiment": {
                            "label": "Bullish news",
                            "sentiment_score": 0.35,
                            "confidence": 0.6,
                            "article_count": 3,
                        },
                    },
                    "news_sentiment": {
                        "label": "Bullish news",
                        "sentiment_score": 0.35,
                        "confidence": 0.6,
                        "article_count": 3,
                    },
                },
            ),
            patch.object(
                market_service,
                "build_intraday_momentum_snapshot",
                return_value={
                    "available": True,
                    "upper_band": 10.6,
                    "lower_band": 9.8,
                    "overlays": {"idm_upper_band": [10.6, 10.6]},
                },
            ),
        ):
            payload = market_service.get_chart_payload(
                ChartRequest(ticker="SPY", interval="5m", points_limit=50)
            )

        self.assertEqual(payload["point_count"], 2)
        self.assertEqual(payload["candles"][0]["volume"], 200.0)
        self.assertEqual(payload["candles"][0]["high"], 10.4)
        self.assertEqual(payload["candles"][0]["low"], 9.9)
        self.assertLess(payload["candles"][0]["datetime"], payload["candles"][1]["datetime"])
        self.assertIn("ema_9", payload["available_indicators"])
        self.assertIn("strategy", payload)
        self.assertIn("idm_upper_band", payload["overlays"])
        self.assertEqual(payload["forecast"]["expected_price"], 10.8)
        self.assertEqual(len(payload["forecast"]["points"]), 5)
        self.assertEqual(payload["forecast_framing"]["target_family"], "volatility_envelope")
        self.assertEqual(payload["forecast_framing"]["benchmark_label"], "No benchmark")
        self.assertEqual(payload["execution_context"]["instrument_type"], "equity")
        self.assertEqual(payload["execution_context"]["preferred_order_type"], "limit")
        self.assertEqual(payload["news_sentiment"]["label"], "Bullish news")

    def test_intraday_momentum_snapshot_flags_long_breakout(self) -> None:
        from backend.services.intraday_momentum_service import build_intraday_momentum_snapshot

        rows = []
        session_dates = pd.bdate_range("2026-03-24", periods=15, tz="America/New_York")

        for session_offset, session_date in enumerate(session_dates):
            session_start = session_date.replace(hour=9, minute=30)

            open_price = 100.0 + session_offset * 0.2
            checkpoint_close = open_price * (1.002 if session_offset < 14 else 1.012)
            close_price = checkpoint_close + 0.1

            rows.extend(
                [
                    {
                        "datetime": session_start,
                        "open": open_price,
                        "high": open_price + 0.15,
                        "low": open_price - 0.10,
                        "close": open_price + 0.05,
                        "volume": 1000,
                    },
                    {
                        "datetime": session_date.replace(hour=10, minute=0),
                        "open": open_price + 0.05,
                        "high": checkpoint_close + 0.05,
                        "low": open_price,
                        "close": checkpoint_close,
                        "volume": 1400,
                    },
                    {
                        "datetime": session_date.replace(hour=10, minute=30),
                        "open": checkpoint_close,
                        "high": close_price + 0.1,
                        "low": checkpoint_close - 0.05,
                        "close": close_price,
                        "volume": 1600,
                    },
                    {
                        "datetime": session_date.replace(hour=16, minute=0),
                        "open": close_price,
                        "high": close_price + 0.12,
                        "low": close_price - 0.08,
                        "close": close_price + 0.05,
                        "volume": 1800,
                    },
                ]
            )

        intraday_frame = pd.DataFrame(rows).set_index("datetime")
        chart_frame = (
            intraday_frame.reset_index()
            .rename(
                columns={
                    "datetime": "datetime",
                    "open": "open",
                    "high": "high",
                    "low": "low",
                    "close": "close",
                    "volume": "volume",
                }
            )
            .tail(4)
            .reset_index(drop=True)
        )

        snapshot = build_intraday_momentum_snapshot(
            "SPY",
            chart_df=chart_frame,
            intraday_frame=intraday_frame,
        )

        self.assertTrue(snapshot["available"])
        self.assertEqual(snapshot["state"], "long")
        self.assertEqual(snapshot["latest_action"], "HOLD LONG")
        self.assertGreater(snapshot["upper_band"], snapshot["lower_band"])
        self.assertIn("idm_upper_band", snapshot["overlays"])
        self.assertEqual(len(snapshot["overlays"]["idm_upper_band"]), len(chart_frame))
        self.assertEqual(snapshot["bias"], "bullish")

    def test_price_forecast_blends_news_bias_and_event_risk(self) -> None:
        from backend.stock_direction_model import ModelConfig, build_price_forecast

        bullish = build_price_forecast(
            latest_close=100.0,
            technical_probability_up=0.58,
            technical_expected_move=0.02,
            atr_pct=0.015,
            settings=ModelConfig(ticker="SPY", horizon=5, interval="5m"),
            event_info={
                "event_risk": False,
                "event_label": "NO EVENT RISK",
                "event_reason": "",
                "next_event_name": "",
                "next_event_date": "",
            },
            news_info={
                "sentiment_score": 0.75,
                "confidence": 0.8,
                "article_count": 6,
                "weighted_article_count": 4.3,
                "label": "Bullish news",
                "lookback_days": 5,
                "updated_at": "2026-04-17T12:00:00+00:00",
                "source": "test",
                "headlines": [],
            },
        )
        risky = build_price_forecast(
            latest_close=100.0,
            technical_probability_up=0.58,
            technical_expected_move=0.02,
            atr_pct=0.015,
            settings=ModelConfig(ticker="SPY", horizon=5, interval="5m"),
            event_info={
                "event_risk": True,
                "event_label": "EVENT RISK",
                "event_reason": "Earnings within 1 day.",
                "next_event_name": "Earnings",
                "next_event_date": "2026-04-18",
            },
            news_info={
                "sentiment_score": 0.75,
                "confidence": 0.8,
                "article_count": 6,
                "weighted_article_count": 4.3,
                "label": "Bullish news",
                "lookback_days": 5,
                "updated_at": "2026-04-17T12:00:00+00:00",
                "source": "test",
                "headlines": [],
            },
        )

        self.assertGreater(bullish["adjusted_probability_up"], bullish["technical_probability_up"])
        self.assertGreater(bullish["expected_price"], 100.0)
        self.assertLess(risky["adjusted_probability_up"], bullish["adjusted_probability_up"])
        self.assertLess(risky["confidence_score"], bullish["confidence_score"])
        self.assertIn("driver_scores", bullish)
        self.assertIn("market_state", bullish)
        self.assertIn("relative_strength", bullish)
        self.assertIn("iv_context", bullish)
        self.assertGreaterEqual(bullish["driver_agreement_score"], 0.0)
        self.assertGreaterEqual(bullish["uncertainty_score"], 0.0)

    def test_price_forecast_uses_intraday_state_layers(self) -> None:
        from backend.stock_direction_model import ModelConfig, build_price_forecast

        forecast = build_price_forecast(
            latest_close=100.0,
            technical_probability_up=0.56,
            technical_expected_move=0.012,
            atr_pct=0.016,
            settings=ModelConfig(ticker="NVDA", horizon=5, interval="5m"),
            event_info={
                "event_risk": False,
                "event_label": "NO EVENT RISK",
                "event_reason": "",
                "next_event_name": "",
                "next_event_date": "",
            },
            news_info={
                "sentiment_score": 0.15,
                "confidence": 0.5,
                "article_count": 2,
                "weighted_article_count": 1.2,
                "label": "Constructive news",
                "lookback_days": 5,
                "updated_at": "2026-04-17T12:00:00+00:00",
                "source": "test",
                "headlines": [],
            },
            market_state={
                "breadth_score": 0.4,
                "breadth_momentum": 0.3,
                "market_trend_score": 0.45,
                "dispersion_score": 0.1,
                "vix_level": 29.0,
                "vix_change_pct": 0.08,
                "vix_term_structure": -0.05,
                "realized_volatility": 0.024,
                "realized_volatility_percentile": 0.88,
                "opening_range_bias": 0.35,
                "opening_range_breakout": 1.0,
                "time_of_day_bucket": "opening",
                "time_of_day_progress": 0.08,
                "source": "test",
            },
            relative_strength={
                "benchmark_symbol": "QQQ",
                "sector_symbol": "SMH",
                "benchmark_relative_return": 0.012,
                "sector_relative_return": 0.009,
                "residual_return": 0.006,
                "peer_momentum_rank": 0.8,
                "peer_count": 4,
                "relative_strength_score": 0.76,
                "source": "test",
            },
            options_flow={
                "implied_volatility": 0.42,
                "iv_rank": 0.82,
                "iv_percentile": 0.85,
                "iv_realized_vol_spread": 0.04,
                "put_call_open_interest_ratio": 0.82,
                "call_volume_pressure": 0.3,
                "put_volume_pressure": 0.1,
                "net_flow_pressure": 0.22,
                "skew_score": 0.08,
                "unusual_volume_score": 0.55,
                "source": "test",
            },
            event_revision={
                "days_to_earnings": 12,
                "event_pressure": 0.0,
                "analyst_revision_score": 0.35,
                "target_revision_score": 0.28,
                "estimate_revision_score": 0.22,
                "macro_sensitivity": 0.4,
                "revision_confidence": 0.7,
                "source": "test",
            },
            ensemble_summary={
                "base_probability_up": 0.58,
                "technical_probability_up": 0.57,
                "microstructure_probability_up": 0.6,
                "relative_strength_probability_up": 0.63,
                "uncertainty_score": 0.28,
                "driver_weights": {"technical": 0.9, "microstructure": 0.7, "relative_strength": 0.8},
                "driver_scores": {"technical": 0.57, "microstructure": 0.6, "relative_strength": 0.63},
                "available_drivers": ["technical", "microstructure", "relative_strength"],
                "calibration_sample_size": 32,
                "empirical_hit_rate": 0.61,
                "purged_split_count": 4,
                "split_embargo_bars": 5,
            },
        )

        self.assertGreater(forecast["state_adjusted_probability_up"], forecast["base_probability_up"])
        self.assertEqual(forecast["volatility_regime"], "elevated")
        self.assertGreater(forecast["relative_strength_score"], 0.7)
        self.assertGreater(forecast["iv_context"]["iv_rank"], 0.8)
        self.assertIn("options_flow", forecast)
        self.assertIn("ensemble_summary", forecast)

    def test_price_forecast_marks_paid_data_quality_and_degrades_fallback_state(self) -> None:
        from backend.stock_direction_model import ModelConfig, build_price_forecast

        common_kwargs = {
            "latest_close": 100.0,
            "technical_probability_up": 0.58,
            "technical_expected_move": 0.014,
            "atr_pct": 0.015,
            "settings": ModelConfig(ticker="SPY", horizon=5, interval="5m"),
            "event_info": {
                "event_risk": False,
                "event_label": "NO EVENT RISK",
                "event_reason": "",
                "next_event_name": "",
                "next_event_date": "",
            },
            "news_info": {
                "sentiment_score": 0.2,
                "confidence": 0.5,
                "article_count": 3,
                "weighted_article_count": 1.8,
                "label": "Constructive news",
                "lookback_days": 5,
                "updated_at": "2026-04-17T12:00:00+00:00",
                "source": "test",
                "headlines": [],
            },
            "ensemble_summary": {
                "base_probability_up": 0.58,
                "technical_probability_up": 0.58,
                "microstructure_probability_up": 0.59,
                "relative_strength_probability_up": 0.6,
                "uncertainty_score": 0.22,
                "driver_weights": {"technical": 0.9, "microstructure": 0.8},
                "driver_scores": {"technical": 0.58, "microstructure": 0.59},
                "available_drivers": ["technical", "microstructure"],
                "calibration_sample_size": 20,
                "empirical_hit_rate": 0.6,
                "purged_split_count": 4,
                "split_embargo_bars": 5,
            },
        }

        premium = build_price_forecast(
            **common_kwargs,
            market_state={
                "breadth_score": 0.3,
                "breadth_momentum": 0.2,
                "market_trend_score": 0.55,
                "dispersion_score": 0.1,
                "vix_level": 20.0,
                "vix_change_pct": 0.01,
                "vix_term_structure": 0.02,
                "realized_volatility": 0.015,
                "realized_volatility_percentile": 0.55,
                "opening_range_bias": 0.12,
                "opening_range_breakout": 0.2,
                "time_of_day_bucket": "morning",
                "time_of_day_progress": 0.25,
                "source": "alpaca",
                "source_detail": "alpaca_market_state",
                "fetched_at": "2026-04-17T12:00:00+00:00",
                "freshness_seconds": 12.0,
                "freshness_ttl_seconds": 90,
                "freshness_status": "fresh",
                "cache_status": "live",
                "degraded": False,
            },
            relative_strength={
                "benchmark_symbol": "SPY",
                "sector_symbol": "XLK",
                "benchmark_relative_return": 0.01,
                "sector_relative_return": 0.008,
                "residual_return": 0.006,
                "peer_momentum_rank": 0.7,
                "peer_count": 5,
                "relative_strength_score": 0.68,
                "source": "alpaca",
                "source_detail": "alpaca_relative_strength",
                "fetched_at": "2026-04-17T12:00:00+00:00",
                "freshness_seconds": 12.0,
                "freshness_ttl_seconds": 90,
                "freshness_status": "fresh",
                "cache_status": "live",
                "degraded": False,
            },
            options_flow={
                "implied_volatility": 0.3,
                "iv_rank": 0.65,
                "iv_percentile": 0.66,
                "iv_realized_vol_spread": 0.03,
                "put_call_open_interest_ratio": 0.9,
                "call_volume_pressure": 0.25,
                "put_volume_pressure": 0.1,
                "net_flow_pressure": 0.12,
                "skew_score": 0.02,
                "unusual_volume_score": 0.4,
                "source": "polygon",
                "source_detail": "polygon_options_chain",
                "fetched_at": "2026-04-17T12:00:00+00:00",
                "freshness_seconds": 28.0,
                "freshness_ttl_seconds": 180,
                "freshness_status": "fresh",
                "cache_status": "live",
                "degraded": False,
            },
            event_revision={
                "days_to_earnings": 18,
                "event_pressure": 0.0,
                "analyst_revision_score": 0.18,
                "target_revision_score": 0.12,
                "estimate_revision_score": 0.1,
                "macro_sensitivity": 0.4,
                "revision_confidence": 0.7,
                "source": "polygon",
                "source_detail": "polygon_reference",
                "fetched_at": "2026-04-17T12:00:00+00:00",
                "freshness_seconds": 90.0,
                "freshness_ttl_seconds": 900,
                "freshness_status": "fresh",
                "cache_status": "live",
                "degraded": False,
            },
        )
        degraded = build_price_forecast(
            **common_kwargs,
            market_state={
                **premium["market_state"],
            },
            relative_strength={
                **premium["relative_strength"],
            },
            options_flow={
                **premium["options_flow"],
                "source": "yfinance",
                "source_detail": "yfinance_proxy",
                "freshness_status": "degraded",
                "cache_status": "fallback",
                "degraded": True,
            },
            event_revision={
                **premium["event_revision"],
                "source": "fallback",
                "source_detail": "fallback_event_revision",
                "freshness_status": "degraded",
                "cache_status": "fallback",
                "degraded": True,
            },
        )

        self.assertEqual(premium["prediction_data_quality"], "full_hybrid_paid_data")
        self.assertFalse(premium["degraded_prediction"])
        self.assertEqual(premium["state_source_map"]["options_flow"], "polygon_options_chain")
        self.assertEqual(degraded["prediction_data_quality"], "degraded_fallback")
        self.assertTrue(degraded["degraded_prediction"])
        self.assertGreater(degraded["uncertainty_score"], premium["uncertainty_score"])
        self.assertEqual(degraded["state_freshness"]["options_flow"]["cache_status"], "fallback")

    def test_hybrid_market_data_provider_prefers_paid_sources_and_uses_fallback_only_when_needed(self) -> None:
        from backend.services.market_data import (
            EventRevisionSnapshot,
            HybridMarketDataProvider,
            OptionsFlowSnapshot,
        )

        class FakeAlpacaClient:
            is_configured = True

            def get_stock_bars(self, symbols, *, interval, period, prepost):
                frames = {}
                for idx, symbol in enumerate(symbols):
                    frames[symbol] = pd.DataFrame(
                        {
                            "timestamp": pd.date_range("2026-04-17T13:30:00Z", periods=30, freq="5min"),
                            "open": [100.0 + idx] * 30,
                            "high": [100.4 + idx] * 30,
                            "low": [99.6 + idx] * 30,
                            "close": [100.1 + idx + (bar * 0.02) for bar in range(30)],
                            "volume": [1000 + (bar * 10) for bar in range(30)],
                        }
                    )
                return frames

            def get_latest_prices(self, symbols):
                return {symbol: 150.0 + offset for offset, symbol in enumerate(symbols)}

        class FakePolygonClient:
            is_configured = True

            def get_options_chain_snapshot(self, ticker, *, expiration=None, limit=250):
                return [
                    {
                        "details": {"contract_type": "call", "strike_price": 150.0},
                        "last_quote": {"bid_price": 4.8, "ask_price": 5.0},
                        "day": {"volume": 180},
                        "open_interest": 900,
                        "implied_volatility": 0.32,
                    },
                    {
                        "details": {"contract_type": "put", "strike_price": 150.0},
                        "last_quote": {"bid_price": 4.2, "ask_price": 4.4},
                        "day": {"volume": 120},
                        "open_interest": 700,
                        "implied_volatility": 0.34,
                    },
                ]

            def get_ticker_details(self, ticker):
                return {"beta": 1.2, "target_price": 165.0, "price": 150.0, "recommendation_mean": 1.8}

            def list_ticker_news(self, ticker, *, limit=10):
                return [{"id": "news-1"}]

            def list_option_contracts(self, ticker, *, expiration=None, limit=250):
                return [{"expiration_date": "2026-05-15"}]

        fallback_provider = MagicMock()
        fallback_provider.download_bars.return_value = pd.DataFrame()
        fallback_provider.get_latest_prices.return_value = {"AAPL": 149.0}
        fallback_provider.get_options_flow_snapshot.return_value = OptionsFlowSnapshot(
            source="yfinance",
            source_detail="yfinance_proxy",
            degraded=True,
        )
        fallback_provider.get_event_revision_snapshot.return_value = EventRevisionSnapshot(
            source="yfinance",
            source_detail="yfinance_proxy",
            degraded=True,
        )

        provider = HybridMarketDataProvider(
            alpaca_client=FakeAlpacaClient(),
            polygon_client=FakePolygonClient(),
            fallback_provider=fallback_provider,
        )

        downloaded = provider.download_bars(["AAPL", "MSFT"], period="5d", interval="5m", prepost=False, group_by="ticker")
        latest_prices = provider.get_latest_prices(["AAPL", "MSFT"], prepost=True)
        options_snapshot = provider.get_options_flow_snapshot("AAPL", underlying_price=150.0)
        event_snapshot = provider.get_event_revision_snapshot("AAPL")

        self.assertFalse(downloaded.empty)
        self.assertEqual(latest_prices["AAPL"], 150.0)
        self.assertEqual(options_snapshot.source, "polygon")
        self.assertFalse(options_snapshot.degraded)
        self.assertEqual(event_snapshot.source, "polygon")
        self.assertFalse(event_snapshot.degraded)
        fallback_provider.download_bars.assert_not_called()
        fallback_provider.get_options_flow_snapshot.assert_not_called()

        fallback_only_provider = HybridMarketDataProvider(
            alpaca_client=FakeAlpacaClient(),
            polygon_client=SimpleNamespace(
                is_configured=False,
                get_options_chain_snapshot=lambda *args, **kwargs: [],
                get_ticker_details=lambda *args, **kwargs: {},
                list_ticker_news=lambda *args, **kwargs: [],
                list_option_contracts=lambda *args, **kwargs: [],
            ),
            fallback_provider=fallback_provider,
        )
        fallback_snapshot = fallback_only_provider.get_options_flow_snapshot("AAPL", underlying_price=150.0)

        self.assertTrue(fallback_snapshot.degraded)
        self.assertEqual(fallback_snapshot.source, "yfinance")
        fallback_provider.get_options_flow_snapshot.assert_called()

    def test_analyze_ticker_fast_mode_skips_news_fetch(self) -> None:
        import numpy as np

        from backend.stock_direction_model import ModelConfig, analyze_ticker

        close = 100 + np.sin(np.linspace(0, 18, 420)) * 6 + np.linspace(0, 8, 420)
        frame = pd.DataFrame(
            {
                "Open": close - 0.4,
                "High": close + 0.9,
                "Low": close - 1.1,
                "Close": close,
                "Volume": np.linspace(1_000_000, 1_400_000, 420),
            },
            index=pd.date_range("2025-01-01", periods=420, freq="h"),
        )

        with patch("backend.stock_direction_model.get_news_sentiment_info", side_effect=AssertionError("fast mode should not fetch live news")):
            report = analyze_ticker(
                ModelConfig(ticker="SPY", horizon=5, interval="1h"),
                make_chart=False,
                preloaded_price_frame=frame,
                include_contract_lookup=False,
                include_event_lookup=False,
                include_alignment=False,
                fast_mode=True,
            )

        self.assertEqual(report["news_sentiment"]["article_count"], 0)
        self.assertEqual(report["news_sentiment"]["source"], "fast-mode")
        self.assertIn(report["vehicle_recommendation"], {"equity", "listed_option", "stand_down"})
        self.assertIn("option_execution_profile", report)
        self.assertIn("base_probability_up", report)
        self.assertIn("state_adjusted_probability_up", report)
        self.assertIn("prediction_data_quality", report)
        self.assertIn("state_source_map", report)
        self.assertIn("state_freshness", report)
        self.assertIn("driver_scores", report)
        self.assertIn("market_state", report)
        self.assertIn("ensemble_summary", report)
        self.assertGreaterEqual(report["uncertainty_score"], 0.0)

    def test_option_vehicle_selector_prefers_equity_when_option_chain_is_weak(self) -> None:
        from backend.stock_direction_model import build_option_execution_profile, select_trade_vehicle

        profile = build_option_execution_profile(
            ticker="MSFT",
            interval="5m",
            close_price=410.0,
            option_plan={
                "action": "BUY CALL",
                "option_side": "CALL",
                "strike_style": "ATM",
                "days_to_expiration": "14-30 DTE",
                "entry_signal": "",
                "entry_low_price": 408.0,
                "entry_high_price": 412.0,
                "sell_signal": "",
                "take_profit_1": 0.3,
                "take_profit_2": 0.6,
                "stop_loss": 0.2,
                "invalidation_price": 404.0,
                "expected_underlying_target": 420.0,
                "recommended_contract": {
                    "contract_symbol": "MSFT260515C00410000",
                    "expiration": (pd.Timestamp.now(tz="UTC") + pd.Timedelta(days=21)).date().isoformat(),
                    "strike": 410.0,
                    "bid": 4.1,
                    "ask": 5.4,
                    "mid": 4.75,
                    "last_price": 4.8,
                    "implied_volatility": 0.28,
                    "volume": 8,
                    "open_interest": 42,
                    "in_the_money": False,
                    "spread_pct": 0.27,
                    "quote_timestamp": (pd.Timestamp.now(tz="UTC") - pd.Timedelta(seconds=45)).isoformat(),
                },
            },
            institutional_flow={
                "score": 0.82,
                "label": "INSTITUTIONAL FLOW STRONG",
                "avg_dollar_volume": 600_000_000.0,
                "median_dollar_volume": 520_000_000.0,
                "controlled_universe": True,
                "option_liquidity_score": 0.25,
                "trade_posture": "clear",
                "event_window_label": "quiet_window",
                "notes": [],
            },
            event_info={
                "event_risk": False,
                "event_label": "NO EVENT RISK",
                "event_reason": "",
                "next_event_name": "",
                "next_event_date": "",
                "next_event_days": None,
                "next_earnings_name": "",
                "next_earnings_date": "",
                "next_earnings_days": None,
                "next_macro_name": "",
                "next_macro_date": "",
                "next_macro_days": None,
                "next_corporate_name": "",
                "next_corporate_date": "",
                "next_corporate_days": None,
                "event_class": "none",
                "event_severity": "low",
                "event_window_label": "quiet_window",
                "session_label": "regular_session",
                "trade_posture": "clear",
                "primary_event_label": "Quiet",
                "summary": "",
                "upcoming_events": [],
            },
        )

        vehicle, reason = select_trade_vehicle(
            verdict="BULLISH",
            trade_decision="VALID TRADE",
            reject_reason="",
            setup_score=81.0,
            option_execution_profile=profile,
        )

        self.assertEqual(vehicle, "equity")
        self.assertIn("option chain", reason.lower())

    def test_option_vehicle_selector_prefers_listed_option_when_chain_is_strong(self) -> None:
        from backend.stock_direction_model import build_option_execution_profile, select_trade_vehicle

        profile = build_option_execution_profile(
            ticker="SPY",
            interval="5m",
            close_price=520.0,
            option_plan={
                "action": "BUY CALL",
                "option_side": "CALL",
                "strike_style": "ATM",
                "days_to_expiration": "14-30 DTE",
                "entry_signal": "",
                "entry_low_price": 518.0,
                "entry_high_price": 522.0,
                "sell_signal": "",
                "take_profit_1": 0.3,
                "take_profit_2": 0.6,
                "stop_loss": 0.2,
                "invalidation_price": 514.0,
                "expected_underlying_target": 529.0,
                "recommended_contract": {
                    "contract_symbol": "SPY260515C00520000",
                    "expiration": (pd.Timestamp.now(tz="UTC") + pd.Timedelta(days=14)).date().isoformat(),
                    "strike": 520.0,
                    "bid": 5.0,
                    "ask": 5.2,
                    "mid": 5.1,
                    "last_price": 5.12,
                    "implied_volatility": 0.21,
                    "volume": 620,
                    "open_interest": 4200,
                    "in_the_money": False,
                    "spread_pct": 0.039,
                    "quote_timestamp": pd.Timestamp.now(tz="UTC").isoformat(),
                },
            },
            institutional_flow={
                "score": 0.9,
                "label": "INSTITUTIONAL FLOW STRONG",
                "avg_dollar_volume": 2_400_000_000.0,
                "median_dollar_volume": 2_150_000_000.0,
                "controlled_universe": True,
                "option_liquidity_score": 0.95,
                "trade_posture": "clear",
                "event_window_label": "quiet_window",
                "notes": [],
            },
            event_info={
                "event_risk": False,
                "event_label": "NO EVENT RISK",
                "event_reason": "",
                "next_event_name": "",
                "next_event_date": "",
                "next_event_days": None,
                "next_earnings_name": "",
                "next_earnings_date": "",
                "next_earnings_days": None,
                "next_macro_name": "",
                "next_macro_date": "",
                "next_macro_days": None,
                "next_corporate_name": "",
                "next_corporate_date": "",
                "next_corporate_days": None,
                "event_class": "none",
                "event_severity": "low",
                "event_window_label": "quiet_window",
                "session_label": "regular_session",
                "trade_posture": "clear",
                "primary_event_label": "Quiet",
                "summary": "",
                "upcoming_events": [],
            },
        )

        vehicle, reason = select_trade_vehicle(
            verdict="BULLISH",
            trade_decision="VALID TRADE",
            reject_reason="",
            setup_score=84.0,
            option_execution_profile=profile,
        )

        self.assertEqual(vehicle, "listed_option")
        self.assertIn("option execution quality", reason.lower())

    def test_option_vehicle_selector_stands_down_when_signal_is_weak(self) -> None:
        from backend.stock_direction_model import build_option_execution_profile, select_trade_vehicle

        profile = build_option_execution_profile(
            ticker="QQQ",
            interval="5m",
            close_price=450.0,
            option_plan={
                "action": "BUY PUT",
                "option_side": "PUT",
                "strike_style": "ATM",
                "days_to_expiration": "14-30 DTE",
                "entry_signal": "",
                "entry_low_price": 448.0,
                "entry_high_price": 452.0,
                "sell_signal": "",
                "take_profit_1": 0.3,
                "take_profit_2": 0.6,
                "stop_loss": 0.2,
                "invalidation_price": 456.0,
                "expected_underlying_target": 440.0,
                "recommended_contract": {
                    "contract_symbol": "QQQ260515P00450000",
                    "expiration": (pd.Timestamp.now(tz="UTC") + pd.Timedelta(days=14)).date().isoformat(),
                    "strike": 450.0,
                    "bid": 4.9,
                    "ask": 5.1,
                    "mid": 5.0,
                    "last_price": 5.0,
                    "implied_volatility": 0.22,
                    "volume": 510,
                    "open_interest": 3900,
                    "in_the_money": False,
                    "spread_pct": 0.04,
                    "quote_timestamp": pd.Timestamp.now(tz="UTC").isoformat(),
                },
            },
            institutional_flow={
                "score": 0.88,
                "label": "INSTITUTIONAL FLOW STRONG",
                "avg_dollar_volume": 1_900_000_000.0,
                "median_dollar_volume": 1_700_000_000.0,
                "controlled_universe": True,
                "option_liquidity_score": 0.94,
                "trade_posture": "clear",
                "event_window_label": "quiet_window",
                "notes": [],
            },
            event_info={
                "event_risk": False,
                "event_label": "NO EVENT RISK",
                "event_reason": "",
                "next_event_name": "",
                "next_event_date": "",
                "next_event_days": None,
                "next_earnings_name": "",
                "next_earnings_date": "",
                "next_earnings_days": None,
                "next_macro_name": "",
                "next_macro_date": "",
                "next_macro_days": None,
                "next_corporate_name": "",
                "next_corporate_date": "",
                "next_corporate_days": None,
                "event_class": "none",
                "event_severity": "low",
                "event_window_label": "quiet_window",
                "session_label": "regular_session",
                "trade_posture": "clear",
                "primary_event_label": "Quiet",
                "summary": "",
                "upcoming_events": [],
            },
        )

        vehicle, reason = select_trade_vehicle(
            verdict="BEARISH",
            trade_decision="PASS",
            reject_reason="Conviction too weak.",
            setup_score=49.0,
            option_execution_profile=profile,
        )

        self.assertEqual(vehicle, "stand_down")
        self.assertIn("conviction too weak", reason.lower())

    def test_option_execution_profile_blocks_zero_dte_for_non_index(self) -> None:
        from backend.stock_direction_model import build_option_execution_profile

        profile = build_option_execution_profile(
            ticker="MSFT",
            interval="5m",
            close_price=410.0,
            option_plan={
                "action": "BUY CALL",
                "option_side": "CALL",
                "strike_style": "ATM",
                "days_to_expiration": "7-14 DTE",
                "entry_signal": "",
                "entry_low_price": 408.0,
                "entry_high_price": 412.0,
                "sell_signal": "",
                "take_profit_1": 0.3,
                "take_profit_2": 0.6,
                "stop_loss": 0.2,
                "invalidation_price": 404.0,
                "expected_underlying_target": 420.0,
                "recommended_contract": {
                    "contract_symbol": "MSFT260422C00410000",
                    "expiration": pd.Timestamp.now(tz="UTC").date().isoformat(),
                    "strike": 410.0,
                    "bid": 3.0,
                    "ask": 3.1,
                    "mid": 3.05,
                    "last_price": 3.04,
                    "implied_volatility": 0.29,
                    "volume": 180,
                    "open_interest": 1200,
                    "in_the_money": False,
                    "spread_pct": 0.033,
                    "quote_timestamp": pd.Timestamp.now(tz="UTC").isoformat(),
                },
            },
            institutional_flow={
                "score": 0.8,
                "label": "INSTITUTIONAL FLOW STRONG",
                "avg_dollar_volume": 900_000_000.0,
                "median_dollar_volume": 850_000_000.0,
                "controlled_universe": True,
                "option_liquidity_score": 0.93,
                "trade_posture": "clear",
                "event_window_label": "quiet_window",
                "notes": [],
            },
            event_info={
                "event_risk": False,
                "event_label": "NO EVENT RISK",
                "event_reason": "",
                "next_event_name": "",
                "next_event_date": "",
                "next_event_days": None,
                "next_earnings_name": "",
                "next_earnings_date": "",
                "next_earnings_days": None,
                "next_macro_name": "",
                "next_macro_date": "",
                "next_macro_days": None,
                "next_corporate_name": "",
                "next_corporate_date": "",
                "next_corporate_days": None,
                "event_class": "none",
                "event_severity": "low",
                "event_window_label": "quiet_window",
                "session_label": "regular_session",
                "trade_posture": "clear",
                "primary_event_label": "Quiet",
                "summary": "",
                "upcoming_events": [],
            },
        )

        self.assertEqual(profile["dte_bucket"], "0dte")
        self.assertIn("0DTE blocked outside SPY/QQQ.", profile["reject_reasons"])
        self.assertEqual(profile["contract_quality_tier"], "weak")

    def test_option_execution_profile_allows_index_zero_dte_when_chain_is_strong(self) -> None:
        from backend.stock_direction_model import build_option_execution_profile

        profile = build_option_execution_profile(
            ticker="SPY",
            interval="5m",
            close_price=520.0,
            option_plan={
                "action": "BUY CALL",
                "option_side": "CALL",
                "strike_style": "ATM",
                "days_to_expiration": "7-14 DTE",
                "entry_signal": "",
                "entry_low_price": 518.0,
                "entry_high_price": 522.0,
                "sell_signal": "",
                "take_profit_1": 0.3,
                "take_profit_2": 0.6,
                "stop_loss": 0.2,
                "invalidation_price": 514.0,
                "expected_underlying_target": 528.0,
                "recommended_contract": {
                    "contract_symbol": "SPY260422C00520000",
                    "expiration": pd.Timestamp.now(tz="UTC").date().isoformat(),
                    "strike": 520.0,
                    "bid": 4.8,
                    "ask": 5.0,
                    "mid": 4.9,
                    "last_price": 4.92,
                    "implied_volatility": 0.24,
                    "volume": 550,
                    "open_interest": 4000,
                    "in_the_money": False,
                    "spread_pct": 0.041,
                    "quote_timestamp": pd.Timestamp.now(tz="UTC").isoformat(),
                },
            },
            institutional_flow={
                "score": 0.92,
                "label": "INSTITUTIONAL FLOW STRONG",
                "avg_dollar_volume": 2_400_000_000.0,
                "median_dollar_volume": 2_100_000_000.0,
                "controlled_universe": True,
                "option_liquidity_score": 0.96,
                "trade_posture": "clear",
                "event_window_label": "quiet_window",
                "notes": [],
            },
            event_info={
                "event_risk": False,
                "event_label": "NO EVENT RISK",
                "event_reason": "",
                "next_event_name": "",
                "next_event_date": "",
                "next_event_days": None,
                "next_earnings_name": "",
                "next_earnings_date": "",
                "next_earnings_days": None,
                "next_macro_name": "",
                "next_macro_date": "",
                "next_macro_days": None,
                "next_corporate_name": "",
                "next_corporate_date": "",
                "next_corporate_days": None,
                "event_class": "none",
                "event_severity": "low",
                "event_window_label": "quiet_window",
                "session_label": "regular_session",
                "trade_posture": "clear",
                "primary_event_label": "Quiet",
                "summary": "",
                "upcoming_events": [],
            },
        )

        self.assertEqual(profile["dte_bucket"], "0dte")
        self.assertNotIn("0DTE blocked outside SPY/QQQ.", profile["reject_reasons"])
        self.assertIn(profile["contract_quality_tier"], {"strong", "acceptable"})

    def test_news_sentiment_uses_article_text_and_relevance(self) -> None:
        from backend.services.market_data.types import MarketNewsItem
        from backend.stock_direction_model import (
            _NEWS_SENTIMENT_CACHE,
            _NEWS_SENTIMENT_CACHE_TS,
            get_news_sentiment_info,
        )

        _NEWS_SENTIMENT_CACHE.clear()
        _NEWS_SENTIMENT_CACHE_TS.clear()

        provider = SimpleNamespace(
            provider_name="composite-news",
            get_news_items=lambda ticker: [
                MarketNewsItem(
                    title="SPY gains after neutral session",
                    publisher="Reuters",
                    url="https://example.com/spy-1",
                    summary="Initial move looked muted.",
                    published_at=pd.Timestamp("2026-04-21T14:00:00Z"),
                    article_text="SPY rallied after traders said the fund beat estimates on inflows and raised guidance for demand.",
                    source_type="article",
                    relevance_score=0.92,
                    mentioned_tickers=("SPY",),
                ),
                MarketNewsItem(
                    title="Macro roundup hits unrelated sectors",
                    publisher="Blog source",
                    url="https://example.com/macro",
                    summary="Broad market recap with limited ticker detail.",
                    published_at=pd.Timestamp("2026-04-21T13:00:00Z"),
                    article_text="The article discusses commodities and regional policy, with no direct SPY setup detail.",
                    source_type="article",
                    relevance_score=0.12,
                    mentioned_tickers=(),
                ),
            ],
        )

        with patch("backend.stock_direction_model.get_market_data_provider", return_value=provider):
            info = get_news_sentiment_info("SPY", lookback_days=5, max_items=5)

        self.assertEqual(info["source"], "composite-news")
        self.assertGreater(info["sentiment_score"], 0.2)
        self.assertGreater(info["confidence"], 0.1)
        self.assertEqual(info["headlines"][0]["source_type"], "article")
        self.assertIn("SPY", info["headlines"][0]["mentioned_tickers"])
        self.assertGreater(info["headlines"][0]["relevance_weight"], info["headlines"][1]["relevance_weight"])

    def test_probability_calibration_uses_recent_empirical_hit_rate(self) -> None:
        from backend.stock_direction_model import _calibrate_probability_from_backtest

        index = pd.date_range("2026-01-01", periods=90, freq="h")
        historical_probabilities = pd.Series([0.15] * 30 + [0.82] * 60, index=index, dtype=float)
        target = pd.Series([0] * 30 + [1] * 54 + [0] * 6, index=index, dtype=float)

        calibrated, empirical_hit_rate, sample_size = _calibrate_probability_from_backtest(
            0.8,
            historical_probabilities,
            target,
        )

        self.assertIsNotNone(empirical_hit_rate)
        self.assertGreater(sample_size, 20)
        self.assertGreater(calibrated, 0.8)
        self.assertLessEqual(calibrated, 0.98)

    def test_forecast_journal_resolves_matured_entries(self) -> None:
        from backend.stock_direction_model import (
            append_forecast_journal_record,
            journal_probability_calibration_summary,
            read_forecast_journal,
            resolve_forecast_journal_entries,
        )

        with _workspace_tempdir() as temp_dir:
            journal_path = Path(temp_dir) / "forecast_journal.csv"
            append_forecast_journal_record(
                {
                    "forecast_at": "2026-01-01T09:30:00+00:00",
                    "ticker": "SPY",
                    "interval": "5m",
                    "horizon": 2,
                    "close": 100.0,
                    "probability_up": 0.7,
                    "technical_probability_up": 0.66,
                    "expected_move": 0.02,
                    "technical_expected_move": 0.015,
                    "expected_price": 102.0,
                    "upper_price": 103.0,
                    "lower_price": 99.5,
                    "forecast_confidence": 0.72,
                    "news_sentiment_score": 0.2,
                    "news_confidence": 0.5,
                    "event_risk": False,
                    "resolved_at": "",
                    "actual_close": float("nan"),
                    "actual_return": float("nan"),
                    "actual_target_up": float("nan"),
                },
                file_path=journal_path,
            )
            history = pd.DataFrame(
                {
                    "Close": [100.0, 100.5, 101.3, 101.9],
                },
                index=pd.to_datetime(
                    [
                        "2026-01-01T09:30:00+00:00",
                        "2026-01-01T09:35:00+00:00",
                        "2026-01-01T09:40:00+00:00",
                        "2026-01-01T09:45:00+00:00",
                    ]
                ),
            )

            resolved = resolve_forecast_journal_entries("SPY", "5m", history, file_path=journal_path)
            summary = journal_probability_calibration_summary("SPY", "5m", file_path=journal_path)

        self.assertFalse(resolved.empty)
        self.assertTrue(str(resolved.iloc[0]["resolved_at"]).strip())
        self.assertGreater(float(resolved.iloc[0]["actual_return"]), 0)
        self.assertEqual(summary["resolved_count"], 1)
        self.assertEqual(summary["empirical_hit_rate"], 1.0)

    def test_build_paired_forecast_journal_records_writes_same_window_baseline_and_hybrid_rows(self) -> None:
        from backend.stock_direction_model import (
            INTRADAY_PREDICTION_STACK_VERSION,
            ModelConfig,
            append_forecast_journal_records,
            build_paired_forecast_journal_records,
            read_forecast_journal,
        )
        import backend.stock_direction_model as sdm

        report = {
            "ticker": "SPY",
            "interval": "5m",
            "close": 100.0,
            "probability_up": 0.61,
            "technical_probability_up": 0.58,
            "expected_move": 0.01,
            "technical_expected_move": 0.008,
            "atr_pct": 0.02,
            "event_risk": False,
            "event_label": "",
            "event_reason": "",
            "next_event_name": "",
            "next_event_date": "",
            "event_context": {
                "event_risk": False,
                "event_label": "",
                "event_reason": "",
                "next_event_name": "",
                "next_event_date": "",
                "event_window_label": "quiet_window",
                "session_label": "morning",
            },
            "news_sentiment": {
                "sentiment_score": 0.0,
                "confidence": 0.0,
                "article_count": 0,
                "weighted_article_count": 0.0,
                "label": "Neutral news",
                "lookback_days": 5,
                "updated_at": "2026-04-23T12:00:00+00:00",
                "source": "test",
                "headlines": [],
            },
            "forecast": {
                "journal_calibration": {},
            },
            "ensemble_summary": {
                "base_probability_up": 0.58,
                "technical_probability_up": 0.58,
                "microstructure_probability_up": 0.58,
                "relative_strength_probability_up": 0.58,
                "uncertainty_score": 0.2,
                "driver_weights": {},
                "driver_scores": {},
                "available_drivers": ["technical"],
                "calibration_sample_size": 0,
                "empirical_hit_rate": None,
                "purged_split_count": 0,
                "split_embargo_bars": 5,
            },
            "market_regime": "trend",
        }
        baseline_report = {
            **report,
            "prediction_data_quality": "degraded_fallback",
            "degraded_prediction": True,
            "state_source_map": {
                "market_state": "yfinance_proxy",
                "relative_strength": "yfinance_proxy",
                "options_flow": "yfinance_proxy",
                "event_revision": "yfinance_proxy",
            },
            "state_freshness": {},
            "market_state": {"source": "yfinance", "source_detail": "yfinance_proxy", "time_of_day_bucket": "morning"},
            "relative_strength": {"source": "yfinance", "source_detail": "yfinance_proxy"},
            "options_flow": {"source": "yfinance", "source_detail": "yfinance_proxy"},
            "event_revision": {"source": "yfinance", "source_detail": "yfinance_proxy"},
            "forecast": {
                "journal_calibration": {},
                "prediction_data_quality": "degraded_fallback",
                "degraded_prediction": True,
                "state_source_map": {
                    "market_state": "yfinance_proxy",
                    "relative_strength": "yfinance_proxy",
                    "options_flow": "yfinance_proxy",
                    "event_revision": "yfinance_proxy",
                },
                "state_freshness": {},
                "market_state": {"source": "yfinance", "source_detail": "yfinance_proxy", "time_of_day_bucket": "morning"},
                "relative_strength": {"source": "yfinance", "source_detail": "yfinance_proxy"},
                "options_flow": {"source": "yfinance", "source_detail": "yfinance_proxy"},
                "event_revision": {"source": "yfinance", "source_detail": "yfinance_proxy"},
                "market_regime": "trend",
                "volatility_regime": "normal",
                "confidence_score": 0.5,
                "upper_price": 101.0,
                "lower_price": 99.0,
                "expected_price": 100.7,
                "contribution_breakdown": {},
            },
        }
        hybrid_report = {
            **report,
            "prediction_data_quality": "full_hybrid_paid_data",
            "degraded_prediction": False,
            "state_source_map": {
                "market_state": "alpaca_market_state",
                "relative_strength": "alpaca_relative_strength",
                "options_flow": "polygon_options_chain",
                "event_revision": "polygon_reference",
            },
            "state_freshness": {},
            "market_state": {"source": "alpaca", "source_detail": "alpaca_market_state", "time_of_day_bucket": "morning"},
            "relative_strength": {"source": "alpaca", "source_detail": "alpaca_relative_strength"},
            "options_flow": {"source": "polygon", "source_detail": "polygon_options_chain"},
            "event_revision": {"source": "polygon", "source_detail": "polygon_reference"},
            "forecast": {
                "journal_calibration": {},
                "prediction_data_quality": "full_hybrid_paid_data",
                "degraded_prediction": False,
                "state_source_map": {
                    "market_state": "alpaca_market_state",
                    "relative_strength": "alpaca_relative_strength",
                    "options_flow": "polygon_options_chain",
                    "event_revision": "polygon_reference",
                },
                "state_freshness": {},
                "market_state": {"source": "alpaca", "source_detail": "alpaca_market_state", "time_of_day_bucket": "morning"},
                "relative_strength": {"source": "alpaca", "source_detail": "alpaca_relative_strength"},
                "options_flow": {"source": "polygon", "source_detail": "polygon_options_chain"},
                "event_revision": {"source": "polygon", "source_detail": "polygon_reference"},
                "market_regime": "trend",
                "volatility_regime": "normal",
                "confidence_score": 0.62,
                "upper_price": 101.4,
                "lower_price": 99.2,
                "expected_price": 100.9,
                "contribution_breakdown": {},
            },
        }
        history = pd.DataFrame({"Close": [99.5, 100.0, 100.2]}, index=pd.date_range("2026-04-23T13:30:00+00:00", periods=3, freq="5min"))

        with _workspace_tempdir() as temp_dir:
            journal_path = Path(temp_dir) / "forecast_journal.csv"
            runtime_settings = type("Settings", (), {"market_data_adapter": "hybrid"})()
            with (
                patch.object(sdm, "settings", runtime_settings),
                patch.object(sdm, "_build_shadow_prediction_report", side_effect=[hybrid_report, baseline_report]),
            ):
                records = build_paired_forecast_journal_records(
                    report,
                    ModelConfig(ticker="SPY", interval="5m", horizon=5),
                    history,
                    forecast_at="2026-04-23T14:00:00+00:00",
                )
                append_forecast_journal_records(records, file_path=journal_path)
                journal = read_forecast_journal(file_path=journal_path)

        self.assertEqual(len(records), 2)
        self.assertEqual(len(journal), 2)
        self.assertEqual(set(journal["prediction_configuration"]), {"proxy_baseline", "full_hybrid"})
        self.assertEqual(journal["prediction_stack_version"].nunique(), 1)
        self.assertEqual(journal["prediction_stack_version"].iloc[0], INTRADAY_PREDICTION_STACK_VERSION)
        self.assertEqual(journal["forecast_group_id"].nunique(), 1)

    def test_price_forecast_uses_journal_calibration_feedback(self) -> None:
        from backend.stock_direction_model import ModelConfig, build_price_forecast

        baseline = build_price_forecast(
            latest_close=100.0,
            technical_probability_up=0.55,
            technical_expected_move=0.01,
            atr_pct=0.02,
            settings=ModelConfig(ticker="SPY", horizon=5, interval="5m"),
            event_info={
                "event_risk": False,
                "event_label": "NO EVENT RISK",
                "event_reason": "",
                "next_event_name": "",
                "next_event_date": "",
            },
            news_info={
                "sentiment_score": 0.0,
                "confidence": 0.0,
                "article_count": 0,
                "weighted_article_count": 0.0,
                "label": "Neutral news",
                "lookback_days": 5,
                "updated_at": "2026-04-17T12:00:00+00:00",
                "source": "test",
                "headlines": [],
            },
            journal_calibration={
                "resolved_count": 24,
                "empirical_hit_rate": 0.72,
                "average_error": 0.11,
                "average_probability_up": 0.54,
            },
        )

        self.assertGreater(baseline["journal_adjusted_probability_up"], baseline["technical_probability_up"])
        self.assertGreater(baseline["adjusted_probability_up"], baseline["technical_probability_up"])
        self.assertGreater(baseline["adjusted_expected_move"], baseline["technical_expected_move"])
        self.assertEqual(baseline["market_regime"], "range")

    def test_weak_regime_reduces_forecast_confidence_and_sizing(self) -> None:
        from backend.stock_direction_model import (
            ModelConfig,
            build_price_forecast,
            calculate_position_sizing,
        )

        strong = build_price_forecast(
            latest_close=100.0,
            technical_probability_up=0.6,
            technical_expected_move=0.015,
            atr_pct=0.02,
            settings=ModelConfig(ticker="SPY", horizon=5, interval="5m"),
            event_info={
                "event_risk": False,
                "event_label": "NO EVENT RISK",
                "event_reason": "",
                "next_event_name": "",
                "next_event_date": "",
            },
            news_info={
                "sentiment_score": 0.0,
                "confidence": 0.0,
                "article_count": 0,
                "weighted_article_count": 0.0,
                "label": "Neutral news",
                "lookback_days": 5,
                "updated_at": "2026-04-17T12:00:00+00:00",
                "source": "test",
                "headlines": [],
            },
            journal_calibration={
                "resolved_count": 20,
                "empirical_hit_rate": 0.62,
                "average_error": 0.11,
                "average_probability_up": 0.54,
                "active_regime": {
                    "market_regime": "range",
                    "resolved_count": 12,
                    "empirical_hit_rate": 0.7,
                    "average_error": 0.09,
                    "average_probability_up": 0.56,
                    "edge": 0.14,
                },
            },
        )
        weak = build_price_forecast(
            latest_close=100.0,
            technical_probability_up=0.6,
            technical_expected_move=0.015,
            atr_pct=0.02,
            settings=ModelConfig(ticker="SPY", horizon=5, interval="5m"),
            event_info={
                "event_risk": False,
                "event_label": "NO EVENT RISK",
                "event_reason": "",
                "next_event_name": "",
                "next_event_date": "",
            },
            news_info={
                "sentiment_score": 0.0,
                "confidence": 0.0,
                "article_count": 0,
                "weighted_article_count": 0.0,
                "label": "Neutral news",
                "lookback_days": 5,
                "updated_at": "2026-04-17T12:00:00+00:00",
                "source": "test",
                "headlines": [],
            },
            journal_calibration={
                "resolved_count": 20,
                "empirical_hit_rate": 0.5,
                "average_error": 0.2,
                "average_probability_up": 0.56,
                "active_regime": {
                    "market_regime": "range",
                    "resolved_count": 10,
                    "empirical_hit_rate": 0.2,
                    "average_error": 0.26,
                    "average_probability_up": 0.58,
                    "edge": -0.38,
                },
            },
        )

        report_strong = {
            "forecast": strong,
            "option_plan": {
                "recommended_contract": {"mid": 2.5},
                "stop_loss": 0.2,
            },
            "trade_decision": "VALID TRADE",
            "reject_reason": "",
        }
        report_weak = {
            "forecast": weak,
            "option_plan": {
                "recommended_contract": {"mid": 2.5},
                "stop_loss": 0.2,
            },
            "trade_decision": "VALID TRADE",
            "reject_reason": "",
        }
        strong_sizing = calculate_position_sizing(report_strong, 10_000, 1.0)
        weak_sizing = calculate_position_sizing(report_weak, 10_000, 1.0)

        self.assertGreater(strong["confidence_score"], weak["confidence_score"])
        self.assertGreater(strong["regime_strength_score"], weak["regime_strength_score"])
        self.assertGreater(strong_sizing["effective_max_risk_dollars"], weak_sizing["effective_max_risk_dollars"])
        self.assertGreaterEqual(strong_sizing["suggested_contracts"], weak_sizing["suggested_contracts"])

    def test_institutional_flow_quality_reduces_setup_score_and_sizing(self) -> None:
        from backend.stock_direction_model import assess_setup, calculate_position_sizing

        contract = {
            "contract_symbol": "SPY260515C00580000",
            "expiration": "2026-05-15",
            "strike": 580.0,
            "bid": 2.4,
            "ask": 2.5,
            "mid": 2.45,
            "last_price": 2.45,
            "implied_volatility": 0.22,
            "volume": 180,
            "open_interest": 1200,
            "in_the_money": False,
            "spread_pct": 0.04,
            "quote_timestamp": "2026-04-21T14:35:00+00:00",
        }
        metrics = {
            "accuracy": 0.63,
            "precision": 0.62,
            "recall": 0.6,
            "roc_auc": 0.68,
        }

        strong_score, _, strong_decision, strong_reason = assess_setup(
            close_price=100.0,
            expected_move=0.02,
            atr_pct=0.015,
            metrics=metrics,
            alignment_label="BULLISH ALIGNMENT",
            conviction_label="HIGH CONVICTION CALL",
            contract=contract,
            entry_low=99.5,
            entry_high=100.5,
            regime_strength_score=0.62,
            institutional_flow_score=0.84,
        )
        weak_score, _, weak_decision, weak_reason = assess_setup(
            close_price=100.0,
            expected_move=0.02,
            atr_pct=0.015,
            metrics=metrics,
            alignment_label="BULLISH ALIGNMENT",
            conviction_label="HIGH CONVICTION CALL",
            contract=contract,
            entry_low=99.5,
            entry_high=100.5,
            regime_strength_score=0.62,
            institutional_flow_score=0.2,
        )

        self.assertGreater(strong_score, weak_score)
        self.assertEqual(strong_decision, "VALID TRADE")
        self.assertEqual(weak_decision, "PASS")
        self.assertIn("Institutional flow support", weak_reason)

        report_strong = {
            "forecast": {"regime_strength_score": 0.62},
            "institutional_flow": {"score": 0.84},
            "option_plan": {
                "recommended_contract": {"mid": 2.45},
                "stop_loss": 0.2,
            },
            "trade_decision": "VALID TRADE",
            "reject_reason": "",
        }
        report_weak = {
            "forecast": {"regime_strength_score": 0.62},
            "institutional_flow": {"score": 0.2},
            "option_plan": {
                "recommended_contract": {"mid": 2.45},
                "stop_loss": 0.2,
            },
            "trade_decision": "VALID TRADE",
            "reject_reason": "",
        }

        strong_sizing = calculate_position_sizing(report_strong, 10_000, 1.0)
        weak_sizing = calculate_position_sizing(report_weak, 10_000, 1.0)

        self.assertGreater(strong_sizing["effective_max_risk_dollars"], weak_sizing["effective_max_risk_dollars"])
        self.assertGreaterEqual(strong_sizing["suggested_contracts"], weak_sizing["suggested_contracts"])

    def test_driver_reliability_downweights_weak_news_signal(self) -> None:
        from backend.stock_direction_model import ModelConfig, build_price_forecast

        neutral = build_price_forecast(
            latest_close=100.0,
            technical_probability_up=0.58,
            technical_expected_move=0.015,
            atr_pct=0.02,
            settings=ModelConfig(ticker="SPY", horizon=5, interval="5m"),
            event_info={
                "event_risk": False,
                "event_label": "NO EVENT RISK",
                "event_reason": "",
                "next_event_name": "",
                "next_event_date": "",
            },
            news_info={
                "sentiment_score": 0.7,
                "confidence": 0.8,
                "article_count": 5,
                "weighted_article_count": 3.6,
                "label": "Bullish news",
                "lookback_days": 5,
                "updated_at": "2026-04-17T12:00:00+00:00",
                "source": "test",
                "headlines": [],
            },
            journal_calibration={
                "resolved_count": 14,
                "empirical_hit_rate": 0.6,
                "average_error": 0.1,
                "average_probability_up": 0.56,
            },
        )
        downweighted = build_price_forecast(
            latest_close=100.0,
            technical_probability_up=0.58,
            technical_expected_move=0.015,
            atr_pct=0.02,
            settings=ModelConfig(ticker="SPY", horizon=5, interval="5m"),
            event_info={
                "event_risk": False,
                "event_label": "NO EVENT RISK",
                "event_reason": "",
                "next_event_name": "",
                "next_event_date": "",
            },
            news_info={
                "sentiment_score": 0.7,
                "confidence": 0.8,
                "article_count": 5,
                "weighted_article_count": 3.6,
                "label": "Bullish news",
                "lookback_days": 5,
                "updated_at": "2026-04-17T12:00:00+00:00",
                "source": "test",
                "headlines": [],
            },
            journal_calibration={
                "resolved_count": 14,
                "empirical_hit_rate": 0.6,
                "average_error": 0.1,
                "average_probability_up": 0.56,
                "driver_attribution": [
                    {
                        "driver": "news",
                        "resolved_count": 10,
                        "helpful_rate": 0.2,
                        "average_contribution": 0.03,
                        "average_signed_impact": -0.04,
                    }
                ],
            },
        )

        self.assertLess(
            downweighted["contribution_breakdown"]["news_driver_weight"],
            neutral["contribution_breakdown"]["news_driver_weight"],
        )
        self.assertLess(
            downweighted["contribution_breakdown"]["news_probability_shift"],
            neutral["contribution_breakdown"]["news_probability_shift"],
        )
        self.assertLess(downweighted["adjusted_probability_up"], neutral["adjusted_probability_up"])

    def test_journal_calibration_prefers_matching_regime_when_available(self) -> None:
        from backend.stock_direction_model import (
            append_forecast_journal_record,
            journal_probability_calibration_summary,
        )

        with _workspace_tempdir() as temp_dir:
            journal_path = Path(temp_dir) / "forecast_journal.csv"
            for index in range(12):
                append_forecast_journal_record(
                    {
                        "forecast_at": f"2026-01-01T09:{index:02d}:00+00:00",
                        "ticker": "SPY",
                        "interval": "5m",
                        "market_regime": "trend",
                        "horizon": 2,
                        "close": 100.0,
                        "probability_up": 0.62,
                        "technical_probability_up": 0.6,
                        "expected_move": 0.02,
                        "technical_expected_move": 0.018,
                        "expected_price": 102.0,
                        "upper_price": 103.0,
                        "lower_price": 99.5,
                        "forecast_confidence": 0.74,
                        "news_sentiment_score": 0.1,
                        "news_confidence": 0.4,
                        "event_risk": False,
                        "resolved_at": "2026-01-01T10:00:00+00:00",
                        "actual_close": 101.5,
                        "actual_return": 0.015,
                        "actual_target_up": 1.0,
                    },
                    file_path=journal_path,
                )
            for index in range(10):
                append_forecast_journal_record(
                    {
                        "forecast_at": f"2026-01-02T09:{index:02d}:00+00:00",
                        "ticker": "SPY",
                        "interval": "5m",
                        "market_regime": "range",
                        "horizon": 2,
                        "close": 100.0,
                        "probability_up": 0.58,
                        "technical_probability_up": 0.57,
                        "expected_move": 0.008,
                        "technical_expected_move": 0.007,
                        "expected_price": 100.8,
                        "upper_price": 101.4,
                        "lower_price": 99.7,
                        "forecast_confidence": 0.62,
                        "news_sentiment_score": 0.0,
                        "news_confidence": 0.1,
                        "event_risk": False,
                        "resolved_at": "2026-01-02T10:00:00+00:00",
                        "actual_close": 99.4,
                        "actual_return": -0.006,
                        "actual_target_up": 0.0,
                    },
                    file_path=journal_path,
                )

            summary = journal_probability_calibration_summary(
                "SPY",
                "5m",
                market_regime="range",
                file_path=journal_path,
            )

        self.assertEqual(summary["calibration_scope"], "regime")
        self.assertEqual(summary["market_regime"], "range")
        self.assertEqual(summary["resolved_count"], 10)
        self.assertEqual(summary["empirical_hit_rate"], 0.0)
        self.assertTrue(summary["regime_breakdown"])
        self.assertEqual(summary["best_regime"]["market_regime"], "trend")
        self.assertEqual(summary["weakest_regime"]["market_regime"], "range")

    def test_driver_attribution_ranks_helpful_and_harmful_signals(self) -> None:
        from backend.stock_direction_model import (
            append_forecast_journal_record,
            journal_probability_calibration_summary,
        )

        with _workspace_tempdir() as temp_dir:
            journal_path = Path(temp_dir) / "forecast_journal.csv"
            rows = [
                {
                    "forecast_at": "2026-01-01T09:30:00+00:00",
                    "ticker": "SPY",
                    "interval": "5m",
                    "market_regime": "trend",
                    "horizon": 2,
                    "close": 100.0,
                    "probability_up": 0.62,
                    "technical_probability_up": 0.6,
                    "expected_move": 0.02,
                    "technical_expected_move": 0.018,
                    "expected_price": 102.0,
                    "upper_price": 103.0,
                    "lower_price": 99.5,
                    "forecast_confidence": 0.74,
                    "technical_confidence_component": 0.12,
                    "news_confidence_component": 0.04,
                    "regime_confidence_component": 0.03,
                    "journal_probability_shift": 0.05,
                    "news_probability_shift": -0.04,
                    "event_probability_shift": 0.0,
                    "news_sentiment_score": -0.2,
                    "news_confidence": 0.4,
                    "event_risk": False,
                    "resolved_at": "2026-01-01T09:40:00+00:00",
                    "actual_close": 101.0,
                    "actual_return": 0.01,
                    "actual_target_up": 1.0,
                },
                {
                    "forecast_at": "2026-01-01T10:00:00+00:00",
                    "ticker": "SPY",
                    "interval": "5m",
                    "market_regime": "trend",
                    "horizon": 2,
                    "close": 100.0,
                    "probability_up": 0.64,
                    "technical_probability_up": 0.61,
                    "expected_move": 0.021,
                    "technical_expected_move": 0.017,
                    "expected_price": 102.1,
                    "upper_price": 103.1,
                    "lower_price": 99.6,
                    "forecast_confidence": 0.75,
                    "technical_confidence_component": 0.13,
                    "news_confidence_component": 0.05,
                    "regime_confidence_component": 0.02,
                    "journal_probability_shift": 0.04,
                    "news_probability_shift": -0.03,
                    "event_probability_shift": 0.0,
                    "news_sentiment_score": -0.15,
                    "news_confidence": 0.35,
                    "event_risk": False,
                    "resolved_at": "2026-01-01T10:10:00+00:00",
                    "actual_close": 101.4,
                    "actual_return": 0.014,
                    "actual_target_up": 1.0,
                },
                {
                    "forecast_at": "2026-01-01T10:30:00+00:00",
                    "ticker": "SPY",
                    "interval": "5m",
                    "market_regime": "range",
                    "horizon": 2,
                    "close": 100.0,
                    "probability_up": 0.38,
                    "technical_probability_up": 0.4,
                    "expected_move": -0.01,
                    "technical_expected_move": -0.008,
                    "expected_price": 99.0,
                    "upper_price": 100.4,
                    "lower_price": 98.8,
                    "forecast_confidence": 0.58,
                    "technical_confidence_component": 0.09,
                    "news_confidence_component": 0.03,
                    "regime_confidence_component": -0.05,
                    "journal_probability_shift": 0.04,
                    "news_probability_shift": -0.02,
                    "event_probability_shift": 0.0,
                    "news_sentiment_score": -0.1,
                    "news_confidence": 0.3,
                    "event_risk": False,
                    "resolved_at": "2026-01-01T10:40:00+00:00",
                    "actual_close": 99.2,
                    "actual_return": -0.008,
                    "actual_target_up": 0.0,
                },
            ]
            for row in rows:
                append_forecast_journal_record(row, file_path=journal_path)

            summary = journal_probability_calibration_summary("SPY", "5m", file_path=journal_path)

        self.assertTrue(summary["driver_attribution"])
        self.assertEqual(summary["best_driver"]["driver"], "technical")
        self.assertEqual(summary["weakest_driver"]["driver"], "news")

    def test_compare_tickers_returns_trade_status_strings(self) -> None:
        from backend.services import market_service

        frame = pd.DataFrame(
            {
                "Open": [100.0, 101.0],
                "High": [102.0, 103.0],
                "Low": [99.0, 100.0],
                "Close": [101.0, 102.0],
                "Volume": [1000, 1100],
            },
            index=pd.date_range("2026-01-01", periods=2, freq="D"),
        )
        report = {
            "ticker": "SPY",
            "verdict": "BULLISH",
            "probability_up": 0.72,
            "forecast": {
                "adjusted_expected_move": 0.03,
                "technical_probability_up": 0.64,
            },
            "setup_score": 81.0,
            "setup_grade": "A setup",
            "conviction_label": "HIGH CONVICTION",
            "alignment_label": "Aligned",
            "trade_decision": "VALID TRADE",
            "reject_reason": "",
            "vehicle_recommendation": "listed_option",
            "vehicle_reason": "Signal quality and option execution quality are both strong.",
            "close": 102.0,
            "option_plan": {
                "option_side": "CALL",
                "entry_low_price": 101.0,
                "entry_high_price": 103.0,
                "expected_underlying_target": 108.0,
                "stop_loss": 0.35,
                "recommended_contract": {"contract_symbol": "SPY260101C00102000"},
            },
            "option_execution_profile": {
                "execution_score": 84.0,
                "contract_quality_tier": "strong",
                "liquidity_tier": "index",
                "quote_age_seconds": 1,
                "dte_bucket": "7_14dte",
                "moneyness_bucket": "near_atm",
                "vehicle_recommendation": "listed_option",
                "vehicle_reason": "Signal quality and option execution quality are both strong.",
                "reject_reasons": [],
            },
        }

        with (
            patch.object(market_service.sdm, "batch_download_ohlcv", return_value={"SPY": frame, "QQQ": frame}),
            patch.object(market_service.sdm, "batch_get_live_prices", return_value={"SPY": 102.0, "QQQ": 102.0}),
            patch.object(market_service.sdm, "analyze_ticker", return_value=report),
            patch.object(market_service.sdm, "get_execution_decision", return_value="BUY NOW"),
            patch.object(market_service.sdm, "evaluate_trade_status", return_value="ENTER NOW"),
            patch.object(market_service, "get_chart_payload", return_value={"candles": [], "overlays": {}}),
        ):
            payload = market_service.compare_tickers(CompareRequest(tickers=["SPY", "QQQ"]))

        self.assertEqual(payload["rows"][0]["execution_action"], "BUY NOW")
        self.assertEqual(payload["rows"][0]["trade_status"], "ENTER NOW")
        self.assertEqual(payload["rows"][0]["direction"], "CALL")
        self.assertEqual(payload["rows"][0]["forecast_framing"]["label"], "Directional move with volatility context")
        self.assertEqual(payload["rows"][0]["forecast_framing"]["benchmark_label"], "Technical base")
        self.assertEqual(payload["rows"][0]["execution_context"]["instrument_type"], "listed_option")
        self.assertEqual(payload["rows"][0]["execution_context"]["preferred_order_type"], "limit")
        self.assertEqual(payload["rows"][0]["vehicle_recommendation"], "listed_option")
        self.assertEqual(payload["rows"][0]["option_execution_score"], 84.0)
        self.assertEqual(payload["rows"][0]["contract_quality_tier"], "strong")
        self.assertEqual(payload["rows"][0]["ranking_context"]["board_name"], "Controlled liquid ranking board")
        self.assertIn(payload["rows"][0]["ranking_context"]["tier"], {"promote", "review", "stand_down"})
        self.assertGreater(payload["rows"][0]["ranking_score"], 0)
        self.assertEqual(payload["summary"]["ranking_board"]["leader"]["ticker"], payload["leader"]["ticker"])
        self.assertEqual(payload["validation_artifact"]["artifact_type"], "candidate_board_snapshot")
        self.assertEqual(payload["validation_artifact"]["leader"]["ticker"], payload["leader"]["ticker"])

    def test_build_watchlist_from_scan_payload_surfaces_ranking_board_summary(self) -> None:
        from backend.schemas import WatchlistRequest
        from backend.services import market_service

        scan_payload = {
            "results": [
                {
                    "ticker": "SPY",
                    "trade_decision": "VALID TRADE",
                    "conviction_label": "HIGH CONVICTION",
                    "entry_low_price": 100.0,
                    "entry_high_price": 102.0,
                    "live_price": 101.0,
                    "ranking_score": 84.2,
                    "ranking_context": {
                        "board_name": "Controlled liquid ranking board",
                        "controlled_universe": True,
                        "tier": "promote",
                        "label": "Promote first",
                        "score": 84.2,
                    },
                    "base_probability_up": 0.61,
                    "state_adjusted_probability_up": 0.64,
                    "uncertainty_score": 0.24,
                    "prediction_data_quality": "full_hybrid_paid_data",
                    "degraded_prediction": False,
                    "state_source_map": {"market_state": "alpaca_market_state"},
                    "state_freshness": {"market_state": {"freshness_status": "fresh"}},
                    "driver_scores": {"technical": 0.6, "microstructure": 0.63},
                    "driver_agreement_score": 0.79,
                    "volatility_regime": "normal",
                    "relative_strength_score": 0.68,
                    "iv_context": {"iv_rank": 0.58, "source": "test"},
                },
                {
                    "ticker": "QQQ",
                    "trade_decision": "PASS",
                    "conviction_label": "MEDIUM CONVICTION",
                    "entry_low_price": 95.0,
                    "entry_high_price": 96.0,
                    "live_price": 90.0,
                    "ranking_score": 59.4,
                    "ranking_context": {
                        "board_name": "Controlled liquid ranking board",
                        "controlled_universe": True,
                        "tier": "review",
                        "label": "Reviewable",
                        "score": 59.4,
                    },
                },
            ],
            "errors": [],
        }

        with patch.object(market_service.sdm, "batch_get_live_prices", return_value={"SPY": 101.0, "QQQ": 90.0}):
            payload = market_service.build_watchlist_from_scan_payload(
                scan_payload,
                WatchlistRequest(tickers=["SPY", "QQQ"], sort_by="ranking_score", descending=True, limit=10),
            )

        self.assertEqual(payload["rows"][0]["ticker"], "SPY")
        self.assertEqual(payload["rows"][0]["board_rank"], 1)
        self.assertEqual(payload["rows"][0]["base_probability_up"], 0.61)
        self.assertEqual(payload["rows"][0]["state_adjusted_probability_up"], 0.64)
        self.assertEqual(payload["rows"][0]["prediction_data_quality"], "full_hybrid_paid_data")
        self.assertFalse(payload["rows"][0]["degraded_prediction"])
        self.assertEqual(payload["rows"][0]["state_source_map"]["market_state"], "alpaca_market_state")
        self.assertEqual(payload["rows"][0]["driver_scores"]["technical"], 0.6)
        self.assertEqual(payload["summary"]["ranking_board"]["promote_count"], 1)
        self.assertEqual(payload["summary"]["ranking_board"]["review_count"], 1)
        self.assertEqual(payload["summary"]["ranking_board"]["leader"]["ticker"], "SPY")
        self.assertEqual(payload["validation_artifact"]["artifact_type"], "candidate_board_snapshot")
        self.assertEqual(payload["validation_artifact"]["summary"]["candidate_count"], 2)

    def test_run_scan_skips_alignment_preloads_when_alignment_disabled(self) -> None:
        from backend.schemas import ScanRequest
        from backend.services import market_service

        frame = pd.DataFrame(
            {
                "Open": [100.0, 101.0],
                "High": [102.0, 103.0],
                "Low": [99.0, 100.0],
                "Close": [101.0, 102.0],
                "Volume": [1000, 1100],
            },
            index=pd.date_range("2026-01-01", periods=2, freq="D"),
        )
        report = {
            "ticker": "SPY",
            "verdict": "BULLISH",
            "probability_up": 0.72,
            "setup_score": 81.0,
            "setup_grade": "A setup",
            "conviction_label": "HIGH CONVICTION",
            "alignment_label": "Aligned",
            "trade_decision": "VALID TRADE",
            "reject_reason": "",
            "close": 102.0,
            "market_regime": "trend",
            "forecast": {"regime_strength_score": 0.66},
            "option_plan": {
                "option_side": "CALL",
                "entry_low_price": 101.0,
                "entry_high_price": 103.0,
                "expected_underlying_target": 108.0,
                "stop_loss": 0.35,
                "recommended_contract": {"contract_symbol": "SPY260101C00102000"},
            },
        }

        market_service.clear_market_response_cache()
        with (
            patch.object(market_service.sdm, "batch_download_ohlcv", return_value={"SPY": frame, "QQQ": frame}) as batch_download_mock,
            patch.object(market_service.sdm, "analyze_ticker", return_value=report),
        ):
            payload = market_service.run_scan(
                ScanRequest(
                    tickers=["SPY", "QQQ"],
                    interval="5m",
                    horizon=5,
                    top_n=2,
                    include_errors=True,
                    include_contract_lookup=False,
                    include_event_lookup=False,
                    include_alignment=False,
                    use_fast_model=True,
                )
            )

        self.assertEqual(batch_download_mock.call_count, 1)
        self.assertEqual(payload["result_count"], 2)
        self.assertEqual(len(payload["results"]), 2)

    def test_compare_tickers_uses_short_lived_cache(self) -> None:
        from backend.services import market_service, ops_service

        frame = pd.DataFrame(
            {
                "Open": [100.0, 101.0],
                "High": [102.0, 103.0],
                "Low": [99.0, 100.0],
                "Close": [101.0, 102.0],
                "Volume": [1000, 1100],
            },
            index=pd.date_range("2026-01-01", periods=2, freq="D"),
        )
        report = {
            "ticker": "SPY",
            "verdict": "BULLISH",
            "probability_up": 0.72,
            "setup_score": 81.0,
            "setup_grade": "A setup",
            "conviction_label": "HIGH CONVICTION",
            "alignment_label": "Aligned",
            "trade_decision": "VALID TRADE",
            "reject_reason": "",
            "close": 102.0,
            "option_plan": {
                "option_side": "CALL",
                "entry_low_price": 101.0,
                "entry_high_price": 103.0,
                "expected_underlying_target": 108.0,
                "stop_loss": 0.35,
                "recommended_contract": {"contract_symbol": "SPY260101C00102000"},
            },
        }

        market_service.clear_market_response_cache()
        ops_service.reset_operation_metrics()
        with (
            patch.object(market_service.sdm, "batch_download_ohlcv", return_value={"SPY": frame, "QQQ": frame}) as batch_download_mock,
            patch.object(market_service.sdm, "batch_get_live_prices", return_value={"SPY": 102.0, "QQQ": 102.0}),
            patch.object(market_service.sdm, "analyze_ticker", return_value=report),
            patch.object(market_service.sdm, "get_execution_decision", return_value="BUY NOW"),
            patch.object(market_service.sdm, "evaluate_trade_status", return_value="ENTER NOW"),
            patch.object(market_service, "get_chart_payload", return_value={"candles": [], "overlays": {}}),
        ):
            first = market_service.compare_tickers(CompareRequest(tickers=["SPY", "QQQ"]))
            second = market_service.compare_tickers(CompareRequest(tickers=["SPY", "QQQ"]))

        snapshot = ops_service.get_operation_metrics_snapshot()

        self.assertEqual(batch_download_mock.call_count, 1)
        self.assertEqual(first["summary"]["count"], second["summary"]["count"])
        self.assertEqual(snapshot["summary"]["cache_hit_count"], 1)
        self.assertEqual(snapshot["operations"][0]["key"], "market.compare")

    def test_notes_agenda_enriches_without_missing_note_ids(self) -> None:
        from backend.services import notes_service

        notes_path = Path("tests") / "_tmp_operator_notes.json"
        try:
            notes_path.write_text(
                json.dumps(
                    [
                        {
                            "id": "blocker",
                            "title": "Blocker",
                            "updated_at": "2026-01-01T00:00:00+00:00",
                        },
                        {
                            "id": "blocked",
                            "title": "Blocked note",
                            "due_at": "2026-04-16T12:00:00+00:00",
                            "blocked_by_ids": ["blocker"],
                            "updated_at": "2026-01-01T00:00:00+00:00",
                        },
                    ]
                ),
                encoding="utf-8",
            )

            with (
                patch.object(notes_service, "NOTES_PATH", notes_path),
                patch.object(notes_service, "_utc_now_dt", return_value=notes_service._parse_dt("2026-04-15T12:00:00+00:00")),
            ):
                payload = notes_service.get_notes_agenda(days=7)
        finally:
            if notes_path.exists():
                notes_path.unlink()

        self.assertEqual(payload["count"], 1)
        self.assertEqual(payload["items"][0]["blocked_state"], "blocked")
        self.assertEqual(payload["items"][0]["progress_state"], "not_started")

    def test_notes_summary_reports_review_loop_progress(self) -> None:
        from backend.services import notes_service

        notes_path = Path("tests") / "_tmp_operator_notes.json"
        try:
            notes_path.write_text(
                json.dumps(
                    [
                        {
                            "id": "repair-open",
                            "title": "Execution drift review",
                            "tags": ["review-loop", "execution"],
                            "ticker": "NVDA",
                            "completed": False,
                            "archived": False,
                            "updated_at": "2026-04-18T09:15:00+00:00",
                        },
                        {
                            "id": "repair-done",
                            "title": "Sizing review resolved",
                            "tags": ["review-loop", "risk"],
                            "ticker": "AAPL",
                            "completed": True,
                            "archived": False,
                            "updated_at": "2026-04-18T10:30:00+00:00",
                        },
                        {
                            "id": "general-note",
                            "title": "General desk note",
                            "tags": ["ops"],
                            "ticker": "SPY",
                            "completed": False,
                            "archived": False,
                            "updated_at": "2026-04-18T08:00:00+00:00",
                        },
                    ]
                ),
                encoding="utf-8",
            )

            with patch.object(notes_service, "NOTES_PATH", notes_path):
                payload = notes_service.get_notes_summary()
        finally:
            if notes_path.exists():
                notes_path.unlink()

        self.assertEqual(payload["review_loop_summary"]["open_count"], 1)
        self.assertEqual(payload["review_loop_summary"]["resolved_count"], 1)
        self.assertEqual(payload["review_loop_summary"]["latest_resolved"]["ticker"], "AAPL")
        self.assertEqual(payload["review_loop_summary"]["latest_resolved"]["title"], "Sizing review resolved")

    def test_demo_auth_is_rejected_when_disabled(self) -> None:
        from backend.services import auth_service

        fake_settings = SimpleNamespace(
            auth_enabled=False,
            allow_demo_auth=False,
            demo_user_id="demo-trader",
            auth_provider="local-demo",
            environment="production",
            demo_user_name="Demo Trader",
            demo_user_email="demo@example.test",
        )
        with patch.object(auth_service, "settings", fake_settings):
            with self.assertRaises(UnauthorizedError):
                auth_service.get_session_payload()

    def test_demo_auth_session_contains_user_id(self) -> None:
        from backend.services import auth_service

        fake_settings = SimpleNamespace(
            auth_enabled=False,
            allow_demo_auth=True,
            demo_user_id="demo-trader",
            auth_provider="local-demo",
            environment="development",
            demo_user_name="Demo Trader",
            demo_user_email="demo@example.test",
        )
        with patch.object(auth_service, "settings", fake_settings):
            payload = auth_service.get_session_payload()

        self.assertEqual(payload["user"]["id"], "demo-trader")
        self.assertEqual(payload["mode"], "demo")

    def test_demo_auth_session_contains_permission_map(self) -> None:
        from backend.services import auth_service

        fake_settings = SimpleNamespace(
            auth_enabled=False,
            allow_demo_auth=True,
            demo_user_id="demo-trader",
            auth_provider="local-demo",
            environment="development",
            demo_user_name="Demo Trader",
            demo_user_email="demo@example.test",
            demo_tenant_slug="alpha-desk",
            demo_tenant_name="Alpha Desk",
            demo_tenant_plan="pro",
        )
        with patch.object(auth_service, "settings", fake_settings):
            payload = auth_service.get_session_payload()

        self.assertTrue(payload["active_tenant"]["permission_map"]["tenant.manage_billing"])
        self.assertTrue(payload["user"]["permission_map"]["tenant.manage_branding"])
        self.assertIn("tenant.create", payload["user"]["permissions"])

    def test_build_demo_user_falls_back_when_demo_tenant_db_lookup_times_out(self) -> None:
        from backend.core import auth as auth_core

        fake_settings = SimpleNamespace(
            demo_user_id="demo-trader",
            demo_user_email="demo@example.test",
            demo_user_name="Demo Trader",
            demo_tenant_slug="alpha-desk",
            demo_tenant_name="Alpha Desk",
            demo_tenant_plan="pro",
            auth_provider="local-demo",
            environment="development",
        )
        request = SimpleNamespace(headers={})

        with (
            patch.object(auth_core, "settings", fake_settings),
            patch.object(
                auth_core,
                "ensure_demo_tenant_for_user",
                side_effect=SQLAlchemyTimeoutError("pool exhausted", None, None),
            ),
        ):
            current_user = auth_core.build_demo_user(request, db=object())

        self.assertEqual(current_user.user_id, "demo-trader")
        self.assertEqual(current_user.tenant_slug, "systematic-equities")
        self.assertEqual(current_user.mode, "demo")
        self.assertTrue(current_user.authenticated)

    def test_build_demo_user_preserves_tenant_identity_when_demo_db_is_locked(self) -> None:
        from backend.core import auth as auth_core

        fake_settings = SimpleNamespace(
            demo_user_id="demo-trader",
            demo_user_email="demo@example.test",
            demo_user_name="Demo Trader",
            demo_tenant_slug="alpha-desk",
            demo_tenant_name="Alpha Desk",
            demo_tenant_plan="pro",
            auth_provider="local-demo",
            environment="development",
        )
        request = SimpleNamespace(headers={})
        fake_tenant = SimpleNamespace(
            id="tenant-123",
            slug="alpha-desk",
            name="Alpha Desk",
            status="active",
            plan_key="pro",
            logo_url=None,
            billing_email="billing@example.test",
            brand_settings={},
        )
        fake_db = MagicMock()

        with (
            patch.object(auth_core, "settings", fake_settings),
            patch.object(
                auth_core,
                "ensure_demo_tenant_for_user",
                side_effect=SQLAlchemyTimeoutError("database is locked", None, None),
            ),
            patch.object(auth_core, "resolve_tenant_by_slug", return_value=fake_tenant),
            patch.object(auth_core, "build_tenant_payload", return_value={"delivery_settings": {}}),
        ):
            current_user = auth_core.build_demo_user(request, db=fake_db)

        fake_db.rollback.assert_called()
        self.assertEqual(current_user.tenant_id, "tenant-123")
        self.assertEqual(current_user.tenant_slug, "alpha-desk")
        self.assertEqual(current_user.tenant_name, "Alpha Desk")
        self.assertTrue(current_user.authenticated)

    def test_local_session_login_provisions_user_cookie_and_tenant_identity(self) -> None:
        from backend.core import auth as auth_core
        from backend.core.database import Base
        from backend.services import auth_provider_service, auth_service, tenant_service

        engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
        Session = sessionmaker(bind=engine, expire_on_commit=False, future=True)
        Base.metadata.create_all(bind=engine)

        fake_settings = SimpleNamespace(
            auth_enabled=True,
            allow_demo_auth=False,
            auth_provider="local-session",
            auth_session_cookie_name="stocksignals_session",
            auth_session_secret="test-local-session-secret",
            auth_session_max_age_seconds=1209600,
            auth_session_secure=False,
            local_auth_allow_signup=True,
            local_auth_default_plan="starter",
            environment="test",
            api_token_salt="local-token-salt",
        )
        with (
            Session() as db,
            patch.object(auth_core, "settings", fake_settings),
            patch.object(auth_provider_service, "settings", fake_settings),
            patch.object(auth_service, "settings", fake_settings),
            patch.object(tenant_service, "settings", fake_settings),
        ):
            result = auth_provider_service.login_with_local_session(
                db,
                email="alex@example.test",
                name="Alex Trader",
            )
            resolved = auth_provider_service.resolve_configured_auth_identity(
                SimpleNamespace(cookies={fake_settings.auth_session_cookie_name: result["cookie_value"]}),
                db,
            )
            current_user = auth_core.build_current_user_from_identity(
                resolved["identity"],
                provider=resolved["provider"],
                mode=resolved["mode"],
            )
            session_payload = auth_service.build_session_payload(current_user)

        self.assertTrue(result["created_organization"])
        self.assertTrue(session_payload["authenticated"])
        self.assertEqual(session_payload["provider"], "local-session")
        self.assertEqual(session_payload["active_tenant"]["plan_key"], "starter")
        self.assertEqual(session_payload["memberships"][0]["tenant"]["slug"], "alex-desk")

    def test_configured_auth_rejects_tampered_local_session_cookie(self) -> None:
        from backend.core.database import Base
        from backend.services import auth_provider_service, tenant_service

        engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
        Session = sessionmaker(bind=engine, expire_on_commit=False, future=True)
        Base.metadata.create_all(bind=engine)

        fake_settings = SimpleNamespace(
            auth_enabled=True,
            allow_demo_auth=False,
            auth_provider="local-session",
            auth_session_cookie_name="stocksignals_session",
            auth_session_secret="test-local-session-secret",
            auth_session_max_age_seconds=1209600,
            auth_session_secure=False,
            local_auth_allow_signup=True,
            local_auth_default_plan="starter",
            environment="test",
            api_token_salt="local-token-salt",
        )
        with (
            Session() as db,
            patch.object(auth_provider_service, "settings", fake_settings),
            patch.object(tenant_service, "settings", fake_settings),
        ):
            result = auth_provider_service.login_with_local_session(
                db,
                email="alex@example.test",
                name="Alex Trader",
            )
            tampered_cookie = f"{result['cookie_value']}tampered"

            with self.assertRaises(UnauthorizedError):
                auth_provider_service.resolve_configured_auth_identity(
                    SimpleNamespace(cookies={fake_settings.auth_session_cookie_name: tampered_cookie}),
                    db,
                )

    def test_auth0_provider_config_reports_ready_when_required_values_exist(self) -> None:
        from backend.services import auth_provider_service

        fake_settings = SimpleNamespace(
            auth_enabled=True,
            allow_demo_auth=False,
            auth_provider="auth0",
            environment="test",
            auth0_domain="tenant.example.auth0.com",
            auth0_client_id="client_123",
            auth0_client_secret="secret_123",
            auth0_audience="https://stocksignals.test/api",
            auth0_scope="openid profile email",
            auth0_organization="org_123",
            public_api_base_url="http://localhost:8010/api",
            auth_post_login_redirect_url="http://localhost:5173/",
            auth_post_logout_redirect_url="http://localhost:5173/",
            local_auth_allow_signup=True,
            auth0_allow_signup=True,
            auth_session_cookie_name="stocksignals_session",
            auth_session_secret="session-secret",
            auth_session_max_age_seconds=1209600,
            auth_state_cookie_name="stocksignals_auth_state",
            auth_state_secret="state-secret",
            auth_state_max_age_seconds=600,
        )
        with patch.object(auth_provider_service, "settings", fake_settings):
            config = auth_provider_service.get_auth_provider_config()

        self.assertTrue(config["supports_login"])
        self.assertTrue(config["auth0"]["ready"])
        self.assertEqual(config["auth0"]["callback_url"], "http://localhost:8010/api/auth/callback")

    def test_auth0_start_and_callback_complete_into_local_session_cookie(self) -> None:
        from backend.core.database import Base
        from backend.services import auth_provider_service

        engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
        Session = sessionmaker(bind=engine, expire_on_commit=False, future=True)
        Base.metadata.create_all(bind=engine)

        fake_settings = SimpleNamespace(
            auth_enabled=True,
            allow_demo_auth=False,
            auth_provider="auth0",
            environment="test",
            auth0_domain="tenant.example.auth0.com",
            auth0_client_id="client_123",
            auth0_client_secret="secret_123",
            auth0_audience="https://stocksignals.test/api",
            auth0_scope="openid profile email",
            auth0_organization="org_123",
            auth0_allow_signup=True,
            public_api_base_url="http://localhost:8010/api",
            auth_post_login_redirect_url="http://localhost:5173/",
            auth_post_logout_redirect_url="http://localhost:5173/",
            local_auth_default_plan="starter",
            auth_session_cookie_name="stocksignals_session",
            auth_session_secret="session-secret",
            auth_session_max_age_seconds=1209600,
            auth_state_cookie_name="stocksignals_auth_state",
            auth_state_secret="state-secret",
            auth_state_max_age_seconds=600,
        )
        with (
            Session() as db,
            patch.object(auth_provider_service, "settings", fake_settings),
            patch.object(auth_provider_service, "_exchange_auth0_code_for_tokens", return_value={"access_token": "token_123"}),
            patch.object(
                auth_provider_service,
                "_fetch_auth0_user_profile",
                return_value={"sub": "auth0|user_123", "email": "auth0@example.test", "name": "Auth Zero"},
            ),
        ):
            start = auth_provider_service.start_auth0_login(requested_tenant_slug="alpha-desk")
            callback = auth_provider_service.complete_auth0_callback(
                db,
                code="code_123",
                state=start["state"],
                request=SimpleNamespace(cookies={fake_settings.auth_state_cookie_name: start["state_cookie_value"]}),
            )
            resolved = auth_provider_service.resolve_configured_auth_identity(
                SimpleNamespace(cookies={fake_settings.auth_session_cookie_name: callback["cookie_value"]}),
                db,
            )

        self.assertIn("/authorize?", start["authorize_url"])
        self.assertTrue(callback["created_organization"])
        self.assertEqual(callback["redirect_url"], "http://localhost:5173/?tenant=alpha-desk")
        self.assertEqual(resolved["provider"], "auth0")
        self.assertEqual(resolved["identity"]["active_tenant"].slug, "auth-desk")

    def test_auth0_invite_token_claims_target_tenant_and_sets_login_hint(self) -> None:
        from backend.core.database import Base
        from backend.models.saas import Tenant, TenantInvitation
        from backend.services import auth_provider_service, tenant_service

        engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
        Session = sessionmaker(bind=engine, expire_on_commit=False, future=True)
        Base.metadata.create_all(bind=engine)

        fake_settings = SimpleNamespace(
            auth_enabled=True,
            allow_demo_auth=False,
            auth_provider="auth0",
            environment="test",
            auth0_domain="tenant.example.auth0.com",
            auth0_client_id="client_123",
            auth0_client_secret="secret_123",
            auth0_audience="https://stocksignals.test/api",
            auth0_scope="openid profile email",
            auth0_organization="org_123",
            auth0_allow_signup=True,
            public_api_base_url="http://localhost:8010/api",
            auth_post_login_redirect_url="http://localhost:5173/",
            auth_post_logout_redirect_url="http://localhost:5173/",
            local_auth_default_plan="starter",
            auth_session_cookie_name="stocksignals_session",
            auth_session_secret="session-secret",
            auth_session_max_age_seconds=1209600,
            auth_state_cookie_name="stocksignals_auth_state",
            auth_state_secret="state-secret",
            auth_state_max_age_seconds=600,
        )
        with (
            Session() as db,
            patch.object(auth_provider_service, "settings", fake_settings),
            patch.object(tenant_service, "settings", fake_settings),
            patch.object(auth_provider_service, "_exchange_auth0_code_for_tokens", return_value={"access_token": "token_123"}),
            patch.object(
                auth_provider_service,
                "_fetch_auth0_user_profile",
                return_value={"sub": "auth0|invite_123", "email": "invitee@example.test", "name": "Invited Trader"},
            ),
        ):
            owner = tenant_service.ensure_user(
                db,
                auth_subject="local:owner@example.test",
                email="owner@example.test",
                name="Owner User",
                provider="local-session",
                platform_role="admin",
            )
            tenant_service.create_tenant(
                db,
                owner=owner,
                name="Pilot Fund",
                plan_key="pro",
                billing_email="owner@example.test",
            )
            pilot_fund = db.execute(select(Tenant).where(Tenant.slug == "pilot-fund")).scalar_one()
            pilot_fund.metadata_json = {
                "delivery": {
                    "auth0_organization": "org_pilot_fund",
                    "auth0_connection": "pilot-fund-saml",
                    "sso_email_domain": "pilotfund.example.com",
                }
            }
            invitation = TenantInvitation(
                tenant_id=pilot_fund.id,
                inviter_user_id=owner.id,
                email="invitee@example.test",
                role="trader",
                status="pending",
                invite_token="invite_pilot_fund_token",
            )
            db.add(invitation)
            db.commit()

            start = auth_provider_service.start_auth0_login(
                db=db,
                invite_token=invitation.invite_token,
                redirect_path="/settings?from=invite",
            )
            callback = auth_provider_service.complete_auth0_callback(
                db,
                code="code_123",
                state=start["state"],
                request=SimpleNamespace(cookies={fake_settings.auth_state_cookie_name: start["state_cookie_value"]}),
            )
            resolved = auth_provider_service.resolve_configured_auth_identity(
                SimpleNamespace(cookies={fake_settings.auth_session_cookie_name: callback["cookie_value"]}),
                db,
            )
            db.refresh(invitation)

        self.assertIn("login_hint=invitee%40example.test", start["authorize_url"])
        self.assertIn("organization=org_pilot_fund", start["authorize_url"])
        self.assertIn("connection=pilot-fund-saml", start["authorize_url"])
        self.assertFalse(callback["created_organization"])
        self.assertIn("/settings?", callback["redirect_url"])
        self.assertIn("from=invite", callback["redirect_url"])
        self.assertIn("tenant=pilot-fund", callback["redirect_url"])
        self.assertEqual(resolved["identity"]["active_tenant"].slug, "pilot-fund")
        self.assertEqual(invitation.status, "accepted")

    def test_auth_entry_context_exposes_tenant_login_and_post_login_paths(self) -> None:
        from backend.core.database import Base
        from backend.models.saas import Tenant
        from backend.services import auth_provider_service, tenant_service

        engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
        Session = sessionmaker(bind=engine, expire_on_commit=False, future=True)
        Base.metadata.create_all(bind=engine)

        fake_settings = SimpleNamespace(
            auth_enabled=True,
            allow_demo_auth=False,
            auth_provider="auth0",
            environment="test",
            auth0_domain="tenant.example.auth0.com",
            auth0_client_id="client_123",
            auth0_client_secret="secret_123",
            auth0_audience="https://stocksignals.test/api",
            auth0_scope="openid profile email",
            auth0_organization="org_123",
            auth0_allow_signup=True,
            public_api_base_url="http://localhost:8010/api",
            auth_post_login_redirect_url="http://localhost:5173/",
            auth_post_logout_redirect_url="http://localhost:5173/",
            local_auth_allow_signup=True,
            local_auth_default_plan="starter",
            auth_session_cookie_name="stocksignals_session",
            auth_session_secret="session-secret",
            auth_session_max_age_seconds=1209600,
            auth_state_cookie_name="stocksignals_auth_state",
            auth_state_secret="state-secret",
            auth_state_max_age_seconds=600,
            api_token_salt="test-token-salt",
            partner_webhook_timeout_seconds=1,
            job_worker_batch_size=12,
        )
        with (
            Session() as db,
            patch.object(auth_provider_service, "settings", fake_settings),
            patch.object(tenant_service, "settings", fake_settings),
        ):
            owner = tenant_service.ensure_user(
                db,
                auth_subject="local:owner@example.test",
                email="owner@example.test",
                name="Owner User",
                provider="local-session",
                platform_role="admin",
            )
            tenant_service.create_tenant(
                db,
                owner=owner,
                name="Pilot Fund",
                plan_key="pro",
                billing_email="owner@example.test",
            )
            pilot_fund = db.execute(select(Tenant).where(Tenant.slug == "pilot-fund")).scalar_one()
            pilot_fund.metadata_json = {
                "delivery": {
                    "auth0_organization": "org_pilot_fund",
                    "auth0_connection": "pilot-fund-saml",
                    "sso_email_domain": "pilotfund.example.com",
                }
            }
            db.commit()

            entry = auth_provider_service.build_auth_entry_context(
                db=db,
                requested_tenant_slug="pilot-fund",
            )

        self.assertEqual(entry["entry_mode"], "tenant-sso")
        self.assertEqual(entry["routing"]["entry_path"], "/login/pilot-fund")
        self.assertEqual(entry["routing"]["post_login_path"], "/?tenant=pilot-fund")
        self.assertEqual(entry["routing"]["redirect_path"], "/?tenant=pilot-fund")
        self.assertEqual(entry["routing"]["connection_hint"], "pilot-fund-saml")
        self.assertEqual(entry["provider_selection"]["recommended_provider"], "auth0")
        self.assertTrue(entry["provider_selection"]["local_login_available"])
        self.assertEqual(entry["tenant"]["slug"], "pilot-fund")

    def test_auth_entry_context_prefers_auth0_for_matching_sso_domain(self) -> None:
        from backend.core.database import Base
        from backend.models.saas import Tenant
        from backend.services import auth_provider_service, tenant_service

        engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
        Session = sessionmaker(bind=engine, expire_on_commit=False, future=True)
        Base.metadata.create_all(bind=engine)

        fake_settings = SimpleNamespace(
            auth_enabled=True,
            allow_demo_auth=False,
            auth_provider="auth0",
            environment="test",
            auth0_domain="tenant.example.auth0.com",
            auth0_client_id="client_123",
            auth0_client_secret="secret_123",
            auth0_audience="https://stocksignals.test/api",
            auth0_scope="openid profile email",
            auth0_organization="org_123",
            auth0_allow_signup=True,
            public_api_base_url="http://localhost:8010/api",
            auth_post_login_redirect_url="http://localhost:5173/",
            auth_post_logout_redirect_url="http://localhost:5173/",
            local_auth_allow_signup=True,
            local_auth_default_plan="starter",
            auth_session_cookie_name="stocksignals_session",
            auth_session_secret="session-secret",
            auth_session_max_age_seconds=1209600,
            auth_state_cookie_name="stocksignals_auth_state",
            auth_state_secret="state-secret",
            auth_state_max_age_seconds=600,
            api_token_salt="test-token-salt",
        )
        with (
            Session() as db,
            patch.object(auth_provider_service, "settings", fake_settings),
            patch.object(tenant_service, "settings", fake_settings),
        ):
            owner = tenant_service.ensure_user(
                db,
                auth_subject="local:owner@example.test",
                email="owner@example.test",
                name="Owner User",
                provider="local-session",
                platform_role="admin",
            )
            tenant_service.create_tenant(
                db,
                owner=owner,
                name="Pilot Fund",
                plan_key="pro",
                billing_email="owner@example.test",
            )
            pilot_fund = db.execute(select(Tenant).where(Tenant.slug == "pilot-fund")).scalar_one()
            pilot_fund.metadata_json = {
                "delivery": {
                    "auth0_organization": "org_pilot_fund",
                    "auth0_connection": "pilot-fund-saml",
                    "sso_email_domain": "pilotfund.example.com",
                    "auth_policy": "prefer_sso",
                }
            }
            db.commit()

            entry = auth_provider_service.build_auth_entry_context(
                db=db,
                requested_tenant_slug="pilot-fund",
                login_email="analyst@pilotfund.example.com",
            )

        self.assertTrue(entry["provider_selection"]["domain_match"])
        self.assertTrue(entry["provider_selection"]["external_login_available"])
        self.assertTrue(entry["provider_selection"]["local_login_available"])
        self.assertFalse(entry["provider_selection"]["block_local_login"])
        self.assertEqual(entry["provider_selection"]["recommended_provider"], "auth0")

    def test_local_session_login_can_fallback_when_auth0_is_active(self) -> None:
        from backend.core.database import Base
        from backend.services import auth_provider_service, tenant_service

        engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
        Session = sessionmaker(bind=engine, expire_on_commit=False, future=True)
        Base.metadata.create_all(bind=engine)

        fake_settings = SimpleNamespace(
            auth_enabled=True,
            allow_demo_auth=False,
            auth_provider="auth0",
            auth0_domain="tenant.example.auth0.com",
            auth0_client_id="client_123",
            auth0_client_secret="secret_123",
            public_api_base_url="http://localhost:8010/api",
            auth_post_login_redirect_url="http://localhost:5173/",
            auth_post_logout_redirect_url="http://localhost:5173/",
            auth_session_cookie_name="stocksignals_session",
            auth_session_secret="session-secret",
            auth_session_max_age_seconds=1209600,
            auth_state_cookie_name="stocksignals_auth_state",
            auth_state_secret="state-secret",
            auth_state_max_age_seconds=600,
            local_auth_allow_signup=True,
            local_auth_default_plan="starter",
            auth0_allow_signup=True,
            environment="test",
            api_token_salt="test-token-salt",
        )
        with (
            Session() as db,
            patch.object(auth_provider_service, "settings", fake_settings),
            patch.object(tenant_service, "settings", fake_settings),
        ):
            result = auth_provider_service.login_with_local_session(
                db,
                email="fallback@example.test",
                name="Fallback User",
            )
            resolved = auth_provider_service.resolve_configured_auth_identity(
                SimpleNamespace(cookies={fake_settings.auth_session_cookie_name: result["cookie_value"]}),
                db,
            )

        self.assertTrue(result["created_organization"])
        self.assertEqual(resolved["provider"], "local-session")

    def test_oidc_provider_config_reports_ready_when_required_values_exist(self) -> None:
        from backend.services import auth_provider_service

        fake_settings = SimpleNamespace(
            auth_enabled=True,
            allow_demo_auth=False,
            auth_provider="oidc",
            environment="test",
            public_api_base_url="http://localhost:8010/api",
            auth_post_login_redirect_url="http://localhost:5173/",
            auth_post_logout_redirect_url="http://localhost:5173/",
            auth_session_cookie_name="stocksignals_session",
            auth_session_secret="session-secret",
            auth_session_max_age_seconds=1209600,
            auth_state_cookie_name="stocksignals_auth_state",
            auth_state_secret="state-secret",
            auth_state_max_age_seconds=600,
            local_auth_allow_signup=True,
            oidc_issuer="https://login.example.test",
            oidc_client_id="oidc_client_123",
            oidc_client_secret="oidc_secret_123",
            oidc_scope="openid profile email",
            oidc_authorize_url="https://login.example.test/oauth2/v1/authorize",
            oidc_token_url="https://login.example.test/oauth2/v1/token",
            oidc_userinfo_url="https://login.example.test/oauth2/v1/userinfo",
            oidc_logout_url="https://login.example.test/logout",
            oidc_allow_signup=True,
        )
        with patch.object(auth_provider_service, "settings", fake_settings):
            config = auth_provider_service.get_auth_provider_config()

        self.assertTrue(config["supports_login"])
        self.assertTrue(config["oidc"]["ready"])
        self.assertEqual(config["oidc"]["callback_url"], "http://localhost:8010/api/auth/callback/oidc")
        self.assertIn("oidc", [provider["key"] for provider in config["available_providers"]])

    def test_oidc_start_and_callback_complete_into_local_session_cookie(self) -> None:
        from backend.core.database import Base
        from backend.services import auth_provider_service

        engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
        Session = sessionmaker(bind=engine, expire_on_commit=False, future=True)
        Base.metadata.create_all(bind=engine)

        fake_settings = SimpleNamespace(
            auth_enabled=True,
            allow_demo_auth=False,
            auth_provider="oidc",
            environment="test",
            public_api_base_url="http://localhost:8010/api",
            auth_post_login_redirect_url="http://localhost:5173/",
            auth_post_logout_redirect_url="http://localhost:5173/",
            auth_session_cookie_name="stocksignals_session",
            auth_session_secret="session-secret",
            auth_session_max_age_seconds=1209600,
            auth_state_cookie_name="stocksignals_auth_state",
            auth_state_secret="state-secret",
            auth_state_max_age_seconds=600,
            local_auth_default_plan="starter",
            local_auth_allow_signup=True,
            oidc_issuer="https://login.example.test",
            oidc_client_id="oidc_client_123",
            oidc_client_secret="oidc_secret_123",
            oidc_scope="openid profile email",
            oidc_authorize_url="https://login.example.test/oauth2/v1/authorize",
            oidc_token_url="https://login.example.test/oauth2/v1/token",
            oidc_userinfo_url="https://login.example.test/oauth2/v1/userinfo",
            oidc_logout_url="https://login.example.test/logout",
            oidc_allow_signup=True,
        )
        with (
            Session() as db,
            patch.object(auth_provider_service, "settings", fake_settings),
            patch.object(auth_provider_service, "_exchange_oidc_code_for_tokens", return_value={"access_token": "token_456"}),
            patch.object(
                auth_provider_service,
                "_fetch_oidc_user_profile",
                return_value={"sub": "oidc|user_456", "email": "oidc@example.test", "name": "OIDC User"},
            ),
        ):
            start = auth_provider_service.start_provider_login(provider="oidc", requested_tenant_slug="alpha-desk")
            callback = auth_provider_service.complete_provider_callback(
                db,
                provider="oidc",
                code="code_456",
                state=start["state"],
                request=SimpleNamespace(cookies={fake_settings.auth_state_cookie_name: start["state_cookie_value"]}),
            )
            resolved = auth_provider_service.resolve_configured_auth_identity(
                SimpleNamespace(cookies={fake_settings.auth_session_cookie_name: callback["cookie_value"]}),
                db,
            )

        self.assertIn("redirect_uri=http%3A%2F%2Flocalhost%3A8010%2Fapi%2Fauth%2Fcallback%2Foidc", start["authorize_url"])
        self.assertTrue(callback["created_organization"])
        self.assertEqual(resolved["provider"], "oidc")
        self.assertEqual(resolved["identity"]["active_tenant"].slug, "oidc-desk")

    def test_auth_entry_context_prefers_oidc_when_tenant_enables_it(self) -> None:
        from backend.core.database import Base
        from backend.models.saas import Tenant
        from backend.services import auth_provider_service, tenant_service

        engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
        Session = sessionmaker(bind=engine, expire_on_commit=False, future=True)
        Base.metadata.create_all(bind=engine)

        fake_settings = SimpleNamespace(
            auth_enabled=True,
            allow_demo_auth=False,
            auth_provider="oidc",
            environment="test",
            public_api_base_url="http://localhost:8010/api",
            auth_post_login_redirect_url="http://localhost:5173/",
            auth_post_logout_redirect_url="http://localhost:5173/",
            auth_session_cookie_name="stocksignals_session",
            auth_session_secret="session-secret",
            auth_session_max_age_seconds=1209600,
            auth_state_cookie_name="stocksignals_auth_state",
            auth_state_secret="state-secret",
            auth_state_max_age_seconds=600,
            local_auth_allow_signup=True,
            local_auth_default_plan="starter",
            oidc_issuer="https://login.example.test",
            oidc_client_id="oidc_client_123",
            oidc_client_secret="oidc_secret_123",
            oidc_scope="openid profile email",
            oidc_authorize_url="https://login.example.test/oauth2/v1/authorize",
            oidc_token_url="https://login.example.test/oauth2/v1/token",
            oidc_userinfo_url="https://login.example.test/oauth2/v1/userinfo",
            oidc_logout_url="https://login.example.test/logout",
            oidc_allow_signup=True,
            api_token_salt="test-token-salt",
        )
        with (
            Session() as db,
            patch.object(auth_provider_service, "settings", fake_settings),
            patch.object(tenant_service, "settings", fake_settings),
        ):
            owner = tenant_service.ensure_user(
                db,
                auth_subject="local:owner@example.test",
                email="owner@example.test",
                name="Owner User",
                provider="local-session",
                platform_role="admin",
            )
            tenant_service.create_tenant(
                db,
                owner=owner,
                name="Pilot Fund",
                plan_key="pro",
                billing_email="owner@example.test",
            )
            pilot_fund = db.execute(select(Tenant).where(Tenant.slug == "pilot-fund")).scalar_one()
            pilot_fund.metadata_json = {
                "delivery": {
                    "enabled_providers": ["oidc", "local-session"],
                    "sso_email_domain": "pilotfund.example.com",
                    "auth_policy": "prefer_sso",
                    "preferred_provider": "oidc",
                }
            }
            db.commit()

            entry = auth_provider_service.build_auth_entry_context(
                db=db,
                requested_tenant_slug="pilot-fund",
                login_email="analyst@pilotfund.example.com",
            )

        self.assertTrue(entry["provider_selection"]["domain_match"])
        self.assertEqual(entry["provider_selection"]["recommended_provider"], "oidc")
        self.assertIn("oidc", entry["provider_selection"]["external_provider_keys"])
        self.assertEqual(entry["routing"]["preferred_provider"], "oidc")

    def test_delivery_settings_persist_tenant_auth_provider_catalog(self) -> None:
        from backend.core.database import Base
        from backend.services import tenant_service

        engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
        Session = sessionmaker(bind=engine, expire_on_commit=False, future=True)
        Base.metadata.create_all(bind=engine)

        fake_settings = SimpleNamespace(
            demo_tenant_slug="alpha-desk",
            demo_tenant_name="Alpha Desk",
            demo_tenant_plan="enterprise",
        )
        with (
            Session() as db,
            patch.object(tenant_service, "settings", fake_settings),
        ):
            identity = tenant_service.ensure_demo_tenant_for_user(
                db,
                auth_subject="demo-trader",
                email="demo@example.test",
                name="Demo Trader",
                provider="local-demo",
                requested_tenant_slug="alpha-desk",
            )
            current_user = SimpleNamespace(
                tenant_id=identity["active_tenant"].id,
                tenant_slug="alpha-desk",
                tenant_name="Alpha Desk",
                tenant_plan="team",
                tenant_status="active",
                user_id="demo-trader",
                auth_subject="demo-trader",
                role="owner",
                platform_role="admin",
            )
            snapshot = tenant_service.update_tenant_delivery_settings(
                db,
                current_user=current_user,
                updates={
                    "enabled_providers": ["local-session", "auth0", "oidc"],
                    "auth_policy": "prefer_sso",
                    "preferred_provider": "default",
                    "auth_provider_records": [
                        {
                            "provider_key": "auth0",
                            "label": "Acme Workforce",
                            "enabled": True,
                            "email_domains": ["acme.example.com"],
                            "organization_hint": "org_acme",
                            "connection_hint": "acme-saml",
                            "is_default": True,
                        },
                        {
                            "provider_key": "oidc",
                            "label": "Beta Enterprise OIDC",
                            "enabled": True,
                            "email_domains": ["beta.example.com"],
                            "is_default": False,
                        },
                    ],
                },
            )

        auth_routing = snapshot["delivery"]["auth_routing"]
        self.assertEqual(auth_routing["provider_record_count"], 2)
        self.assertEqual(auth_routing["provider_domain_count"], 2)
        self.assertEqual(auth_routing["provider"], "auth0")
        self.assertEqual(auth_routing["provider_records"][0]["label"], "Acme Workforce")
        self.assertTrue(auth_routing["provider_records"][0]["is_default"])

    def test_auth_entry_and_start_use_matching_tenant_provider_record(self) -> None:
        from backend.core.database import Base
        from backend.models.saas import Tenant
        from backend.services import auth_provider_service, tenant_service

        engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
        Session = sessionmaker(bind=engine, expire_on_commit=False, future=True)
        Base.metadata.create_all(bind=engine)

        fake_settings = SimpleNamespace(
            auth_enabled=True,
            allow_demo_auth=False,
            auth_provider="auth0",
            environment="test",
            auth0_domain="tenant.example.auth0.com",
            auth0_client_id="client_123",
            auth0_client_secret="secret_123",
            auth0_audience="https://stocksignals.test/api",
            auth0_scope="openid profile email",
            auth0_organization="org_default",
            auth0_allow_signup=True,
            public_api_base_url="http://localhost:8010/api",
            auth_post_login_redirect_url="http://localhost:5173/",
            auth_post_logout_redirect_url="http://localhost:5173/",
            local_auth_allow_signup=True,
            local_auth_default_plan="starter",
            auth_session_cookie_name="stocksignals_session",
            auth_session_secret="session-secret",
            auth_session_max_age_seconds=1209600,
            auth_state_cookie_name="stocksignals_auth_state",
            auth_state_secret="state-secret",
            auth_state_max_age_seconds=600,
            api_token_salt="test-token-salt",
        )
        with (
            Session() as db,
            patch.object(auth_provider_service, "settings", fake_settings),
            patch.object(tenant_service, "settings", fake_settings),
        ):
            owner = tenant_service.ensure_user(
                db,
                auth_subject="local:owner@example.test",
                email="owner@example.test",
                name="Owner User",
                provider="local-session",
                platform_role="admin",
            )
            tenant_service.create_tenant(
                db,
                owner=owner,
                name="Pilot Fund",
                plan_key="pro",
                billing_email="owner@example.test",
            )
            pilot_fund = db.execute(select(Tenant).where(Tenant.slug == "pilot-fund")).scalar_one()
            pilot_fund.metadata_json = {
                "delivery": {
                    "enabled_providers": ["local-session", "auth0"],
                    "auth_policy": "prefer_sso",
                    "auth_provider_records": [
                        {
                            "id": "provider_auth0_acme",
                            "provider_key": "auth0",
                            "label": "Acme Workforce",
                            "enabled": True,
                            "email_domains": ["acme.example.com"],
                            "organization_hint": "org_acme",
                            "connection_hint": "acme-saml",
                            "auth0_domain": "acme.example.auth0.com",
                            "client_id": "tenant_client_123",
                            "client_secret": "tenant_secret_123",
                            "audience": "https://acme.example.test/api",
                            "scope": "openid profile email",
                            "health_status": "ready",
                            "health_message": "Validated.",
                            "is_default": True,
                        }
                    ],
                }
            }
            db.commit()

            entry = auth_provider_service.build_auth_entry_context(
                db=db,
                requested_tenant_slug="pilot-fund",
                login_email="analyst@acme.example.com",
            )
            start = auth_provider_service.start_provider_login(
                provider="auth0",
                db=db,
                requested_tenant_slug="pilot-fund",
                login_email="analyst@acme.example.com",
            )

        self.assertTrue(entry["provider_selection"]["domain_match"])
        self.assertEqual(entry["provider_selection"]["recommended_provider"], "auth0")
        self.assertEqual(entry["provider_selection"]["recommended_provider_record_id"], "provider_auth0_acme")
        self.assertTrue(
            any(
                option.get("provider_record_id") == "provider_auth0_acme" and option.get("label") == "Acme Workforce"
                for option in entry["provider_selection"]["providers"]
            )
        )
        self.assertEqual(start["provider_record_id"], "provider_auth0_acme")
        self.assertIn("client_id=tenant_client_123", start["authorize_url"])
        self.assertIn("organization=org_acme", start["authorize_url"])
        self.assertIn("connection=acme-saml", start["authorize_url"])
        self.assertIn("login_hint=analyst%40acme.example.com", start["authorize_url"])

    def test_oidc_tenant_provider_record_can_supply_runtime_config(self) -> None:
        from backend.core.database import Base
        from backend.models.saas import Tenant
        from backend.services import auth_provider_service, tenant_service

        engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
        Session = sessionmaker(bind=engine, expire_on_commit=False, future=True)
        Base.metadata.create_all(bind=engine)

        fake_settings = SimpleNamespace(
            auth_enabled=True,
            allow_demo_auth=False,
            auth_provider="oidc",
            environment="test",
            public_api_base_url="http://localhost:8010/api",
            auth_post_login_redirect_url="http://localhost:5173/",
            auth_post_logout_redirect_url="http://localhost:5173/",
            auth_session_cookie_name="stocksignals_session",
            auth_session_secret="session-secret",
            auth_session_max_age_seconds=1209600,
            auth_state_cookie_name="stocksignals_auth_state",
            auth_state_secret="state-secret",
            auth_state_max_age_seconds=600,
            local_auth_allow_signup=True,
            local_auth_default_plan="starter",
            oidc_issuer="",
            oidc_client_id="",
            oidc_client_secret="",
            oidc_scope="openid profile email",
            oidc_authorize_url="",
            oidc_token_url="",
            oidc_userinfo_url="",
            oidc_logout_url="",
            oidc_allow_signup=True,
            api_token_salt="test-token-salt",
        )
        with (
            Session() as db,
            patch.object(auth_provider_service, "settings", fake_settings),
            patch.object(tenant_service, "settings", fake_settings),
        ):
            owner = tenant_service.ensure_user(
                db,
                auth_subject="local:owner@example.test",
                email="owner@example.test",
                name="Owner User",
                provider="local-session",
                platform_role="admin",
            )
            tenant_service.create_tenant(
                db,
                owner=owner,
                name="Pilot Fund",
                plan_key="pro",
                billing_email="owner@example.test",
            )
            pilot_fund = db.execute(select(Tenant).where(Tenant.slug == "pilot-fund")).scalar_one()
            pilot_fund.metadata_json = {
                "delivery": {
                    "enabled_providers": ["oidc"],
                    "auth_policy": "prefer_sso",
                    "preferred_provider": "oidc",
                    "auth_provider_records": [
                        {
                            "id": "provider_oidc_beta",
                            "provider_key": "oidc",
                            "label": "Beta OIDC",
                            "enabled": True,
                            "email_domains": ["beta.example.com"],
                            "issuer": "https://login.beta.example.com",
                            "authorize_url": "https://login.beta.example.com/oauth2/v1/authorize",
                            "token_url": "https://login.beta.example.com/oauth2/v1/token",
                            "userinfo_url": "https://login.beta.example.com/oauth2/v1/userinfo",
                            "logout_url": "https://login.beta.example.com/logout",
                            "client_id": "beta_client_123",
                            "client_secret": "beta_secret_123",
                            "scope": "openid profile email",
                            "health_status": "ready",
                            "health_message": "Validated.",
                            "is_default": True,
                        }
                    ],
                }
            }
            db.commit()

            entry = auth_provider_service.build_auth_entry_context(
                db=db,
                requested_tenant_slug="pilot-fund",
                login_email="ops@beta.example.com",
            )
            start = auth_provider_service.start_provider_login(
                provider="oidc",
                db=db,
                requested_tenant_slug="pilot-fund",
                login_email="ops@beta.example.com",
            )

        self.assertEqual(entry["provider_selection"]["recommended_provider"], "oidc")
        self.assertTrue(entry["provider_selection"]["domain_match"])
        self.assertTrue(entry["provider_selection"]["provider_records"][0]["has_client_secret"])
        self.assertNotIn("client_secret", entry["provider_selection"]["provider_records"][0])
        self.assertIn("client_id=beta_client_123", start["authorize_url"])
        self.assertIn("login_hint=ops%40beta.example.com", start["authorize_url"])

    def test_delivery_action_validates_tenant_auth_provider_and_persists_health(self) -> None:
        from backend.core.database import Base
        from backend.services import auth_provider_service, tenant_service

        engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
        Session = sessionmaker(bind=engine, expire_on_commit=False, future=True)
        Base.metadata.create_all(bind=engine)

        fake_settings = SimpleNamespace(
            demo_tenant_slug="alpha-desk",
            demo_tenant_name="Alpha Desk",
            demo_tenant_plan="enterprise",
            allow_demo_auth=False,
            public_api_base_url="http://localhost:8010/api",
            auth_post_logout_redirect_url="http://localhost:5173/",
            auth0_allow_signup=True,
            oidc_allow_signup=True,
        )
        with (
            Session() as db,
            patch.object(tenant_service, "settings", fake_settings),
            patch.object(auth_provider_service, "settings", fake_settings),
            patch.object(
                auth_provider_service,
                "_fetch_json",
                return_value={
                    "authorization_endpoint": "https://acme.example.auth0.com/authorize",
                    "token_endpoint": "https://acme.example.auth0.com/oauth/token",
                    "userinfo_endpoint": "https://acme.example.auth0.com/userinfo",
                    "end_session_endpoint": "https://acme.example.auth0.com/v2/logout",
                },
            ),
        ):
            identity = tenant_service.ensure_demo_tenant_for_user(
                db,
                auth_subject="demo-trader",
                email="demo@example.test",
                name="Demo Trader",
                provider="local-demo",
                requested_tenant_slug="alpha-desk",
            )
            current_user = SimpleNamespace(
                tenant_id=identity["active_tenant"].id,
                tenant_slug="alpha-desk",
                tenant_name="Alpha Desk",
                tenant_plan="team",
                tenant_status="active",
                user_id="demo-trader",
                auth_subject="demo-trader",
                role="owner",
                platform_role="admin",
            )
            tenant_service.update_tenant_delivery_settings(
                db,
                current_user=current_user,
                updates={
                    "enabled_providers": ["auth0"],
                    "auth_policy": "prefer_sso",
                    "auth_provider_records": [
                        {
                            "provider_id": "provider_auth0_acme",
                            "provider_key": "auth0",
                            "label": "Acme Workforce",
                            "enabled": True,
                            "email_domains": ["acme.example.com"],
                            "auth0_domain": "acme.example.auth0.com",
                            "client_id": "tenant_client_123",
                            "client_secret": "tenant_secret_123",
                            "is_default": True,
                        }
                    ],
                },
            )
            snapshot = tenant_service.run_tenant_delivery_action(
                db,
                current_user=current_user,
                action="validate_auth_provider",
                provider_id="provider_auth0_acme",
            )

        record = snapshot["delivery"]["auth_routing"]["provider_records"][0]
        self.assertEqual(record["health_status"], "ready")
        self.assertTrue(record["has_client_secret"])
        self.assertEqual(record["discovery_source"], "auth0-well-known")
        self.assertIn("/oauth/token", record["resolved_token_url"])
        self.assertEqual(snapshot["delivery"]["auth_routing"]["provider_health"]["ready"], 1)

    def test_delivery_action_can_clear_tenant_auth_provider_secret(self) -> None:
        from backend.core.database import Base
        from backend.services import tenant_service

        engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
        Session = sessionmaker(bind=engine, expire_on_commit=False, future=True)
        Base.metadata.create_all(bind=engine)

        fake_settings = SimpleNamespace(
            demo_tenant_slug="alpha-desk",
            demo_tenant_name="Alpha Desk",
            demo_tenant_plan="enterprise",
        )
        with (
            Session() as db,
            patch.object(tenant_service, "settings", fake_settings),
        ):
            identity = tenant_service.ensure_demo_tenant_for_user(
                db,
                auth_subject="demo-trader",
                email="demo@example.test",
                name="Demo Trader",
                provider="local-demo",
                requested_tenant_slug="alpha-desk",
            )
            current_user = SimpleNamespace(
                tenant_id=identity["active_tenant"].id,
                tenant_slug="alpha-desk",
                tenant_name="Alpha Desk",
                tenant_plan="team",
                tenant_status="active",
                user_id="demo-trader",
                auth_subject="demo-trader",
                role="owner",
                platform_role="admin",
            )
            tenant_service.update_tenant_delivery_settings(
                db,
                current_user=current_user,
                updates={
                    "enabled_providers": ["oidc"],
                    "auth_policy": "prefer_sso",
                    "auth_provider_records": [
                        {
                            "provider_id": "provider_oidc_beta",
                            "provider_key": "oidc",
                            "label": "Beta OIDC",
                            "enabled": True,
                            "email_domains": ["beta.example.com"],
                            "issuer": "https://login.beta.example.com",
                            "client_id": "beta_client_123",
                            "client_secret": "beta_secret_123",
                            "is_default": True,
                        }
                    ],
                },
            )
            snapshot = tenant_service.run_tenant_delivery_action(
                db,
                current_user=current_user,
                action="rotate_auth_provider_secret",
                provider_id="provider_oidc_beta",
            )

        record = snapshot["delivery"]["auth_routing"]["provider_records"][0]
        self.assertFalse(record["has_client_secret"])
        self.assertEqual(record["health_status"], "incomplete")
        self.assertIn("Client secret cleared", record["health_message"])
        self.assertEqual(snapshot["delivery"]["auth_routing"]["provider_health"]["incomplete"], 1)

    def test_delivery_update_stages_secret_and_can_promote_it_live(self) -> None:
        from backend.core.database import Base
        from backend.models.saas import Tenant
        from backend.services import auth_provider_service, tenant_service

        engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
        Session = sessionmaker(bind=engine, expire_on_commit=False, future=True)
        Base.metadata.create_all(bind=engine)

        fake_settings = SimpleNamespace(
            demo_tenant_slug="alpha-desk",
            demo_tenant_name="Alpha Desk",
            demo_tenant_plan="enterprise",
            public_api_base_url="http://localhost:8010/api",
            auth_post_logout_redirect_url="http://localhost:5173/",
            auth0_allow_signup=True,
        )
        with (
            Session() as db,
            patch.object(tenant_service, "settings", fake_settings),
            patch.object(auth_provider_service, "settings", fake_settings),
            patch.object(
                auth_provider_service,
                "_fetch_json",
                return_value={
                    "authorization_endpoint": "https://acme.example.auth0.com/authorize",
                    "token_endpoint": "https://acme.example.auth0.com/oauth/token",
                    "userinfo_endpoint": "https://acme.example.auth0.com/userinfo",
                    "end_session_endpoint": "https://acme.example.auth0.com/v2/logout",
                },
            ),
        ):
            identity = tenant_service.ensure_demo_tenant_for_user(
                db,
                auth_subject="demo-trader",
                email="demo@example.test",
                name="Demo Trader",
                provider="local-demo",
                requested_tenant_slug="alpha-desk",
            )
            current_user = SimpleNamespace(
                tenant_id=identity["active_tenant"].id,
                tenant_slug="alpha-desk",
                tenant_name="Alpha Desk",
                tenant_plan="team",
                tenant_status="active",
                user_id="demo-trader",
                auth_subject="demo-trader",
                role="owner",
                platform_role="admin",
            )
            tenant_service.update_tenant_delivery_settings(
                db,
                current_user=current_user,
                updates={
                    "enabled_providers": ["auth0"],
                    "auth_policy": "prefer_sso",
                    "auth_provider_records": [
                        {
                            "provider_id": "provider_auth0_acme",
                            "provider_key": "auth0",
                            "label": "Acme Workforce",
                            "enabled": True,
                            "email_domains": ["acme.example.com"],
                            "auth0_domain": "acme.example.auth0.com",
                            "client_id": "tenant_client_123",
                            "client_secret": "tenant_secret_live",
                            "is_default": True,
                        }
                    ],
                },
            )
            staged = tenant_service.update_tenant_delivery_settings(
                db,
                current_user=current_user,
                updates={
                    "auth_provider_records": [
                        {
                            "provider_id": "provider_auth0_acme",
                            "provider_key": "auth0",
                            "label": "Acme Workforce",
                            "enabled": True,
                            "email_domains": ["acme.example.com"],
                            "auth0_domain": "acme.example.auth0.com",
                            "client_id": "tenant_client_123",
                            "client_secret": "tenant_secret_next",
                            "is_default": True,
                        }
                    ],
                },
            )
            tenant = db.execute(select(Tenant).where(Tenant.slug == identity["active_tenant"].slug)).scalar_one()
            stored_record = tenant.metadata_json["delivery"]["auth_provider_records"][0]
            validated = tenant_service.run_tenant_delivery_action(
                db,
                current_user=current_user,
                action="validate_auth_provider",
                provider_id="provider_auth0_acme",
            )
            promoted = tenant_service.run_tenant_delivery_action(
                db,
                current_user=current_user,
                action="promote_auth_provider_secret",
                provider_id="provider_auth0_acme",
            )
            tenant = db.execute(select(Tenant).where(Tenant.slug == identity["active_tenant"].slug)).scalar_one()
            promoted_record = tenant.metadata_json["delivery"]["auth_provider_records"][0]

        staged_record = staged["delivery"]["auth_routing"]["provider_records"][0]
        self.assertTrue(staged_record["has_client_secret"])
        self.assertTrue(staged_record["has_pending_client_secret"])
        self.assertEqual(stored_record["client_secret"], "tenant_secret_live")
        self.assertEqual(stored_record["pending_client_secret"], "tenant_secret_next")
        validated_record = validated["delivery"]["auth_routing"]["provider_records"][0]
        self.assertEqual(validated_record["pending_health_status"], "ready")
        promoted_snapshot_record = promoted["delivery"]["auth_routing"]["provider_records"][0]
        self.assertFalse(promoted_snapshot_record["has_pending_client_secret"])
        self.assertEqual(promoted_snapshot_record["health_status"], "ready")
        self.assertEqual(promoted["delivery"]["auth_routing"]["provider_health"]["ready"], 1)
        self.assertTrue(promoted["delivery"]["auth_routing"]["last_ready_at"])
        self.assertTrue(promoted["delivery"]["auth_routing"]["recent_operations"])
        self.assertEqual(promoted_record["client_secret"], "tenant_secret_next")
        self.assertIsNone(promoted_record.get("pending_client_secret"))

    def test_require_sso_with_unready_tenant_provider_sets_launch_blocker(self) -> None:
        from backend.core.database import Base
        from backend.models.saas import Tenant
        from backend.services import auth_provider_service, tenant_service

        engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
        Session = sessionmaker(bind=engine, expire_on_commit=False, future=True)
        Base.metadata.create_all(bind=engine)

        fake_settings = SimpleNamespace(
            demo_tenant_slug="alpha-desk",
            demo_tenant_name="Alpha Desk",
            demo_tenant_plan="enterprise",
            auth_enabled=True,
            allow_demo_auth=False,
            auth_provider="auth0",
            environment="test",
            auth0_domain="https://shared.example.auth0.com",
            auth0_client_id="shared_client",
            auth0_client_secret="shared_secret",
            auth0_scope="openid profile email",
            public_api_base_url="http://localhost:8010/api",
            auth_post_login_redirect_url="http://localhost:5173",
            auth_post_logout_redirect_url="http://localhost:5173/",
            local_auth_allow_signup=True,
            local_auth_default_plan="starter",
            auth_session_cookie_name="stocksignals_session",
            auth_session_secret="session-secret",
            auth_session_max_age_seconds=1209600,
            auth_state_cookie_name="stocksignals_auth_state",
            auth_state_secret="state-secret",
            auth_state_max_age_seconds=600,
            oidc_issuer="",
            oidc_authorize_url="",
            oidc_token_url="",
            oidc_userinfo_url="",
            oidc_logout_url="",
            oidc_client_id="",
            oidc_client_secret="",
            oidc_scope="openid profile email",
            api_token_salt="test-token-salt",
        )
        with (
            Session() as db,
            patch.object(tenant_service, "settings", fake_settings),
            patch.object(auth_provider_service, "settings", fake_settings),
        ):
            owner = tenant_service.ensure_user(
                db,
                auth_subject="local:owner@example.test",
                email="owner@example.test",
                name="Owner User",
                provider="local-session",
                platform_role="admin",
            )
            tenant_service.create_tenant(
                db,
                owner=owner,
                name="Pilot Fund",
                plan_key="pro",
                billing_email="owner@example.test",
            )
            pilot_fund = db.execute(select(Tenant).where(Tenant.slug == "pilot-fund")).scalar_one()
            pilot_fund.metadata_json = {
                "delivery": {
                    "enabled_providers": ["local-session", "auth0"],
                    "auth_policy": "require_sso",
                    "preferred_provider": "auth0",
                    "auth_provider_records": [
                        {
                            "id": "provider_auth0_pilot",
                            "provider_key": "auth0",
                            "label": "Pilot Workforce",
                            "enabled": True,
                            "email_domains": ["pilot.example.com"],
                            "auth0_domain": "pilot.example.auth0.com",
                            "client_id": "pilot_client",
                            "client_secret": "",
                            "is_default": True,
                        }
                    ],
                }
            }
            db.commit()

            entry = auth_provider_service.build_auth_entry_context(
                db=db,
                requested_tenant_slug="pilot-fund",
                login_email="ops@pilot.example.com",
            )

        selection = entry["provider_selection"]
        self.assertFalse(selection["launch_ready"])
        self.assertTrue(selection["launch_blockers"])
        self.assertTrue(selection["local_login_available"])
        self.assertEqual(selection["recommended_provider"], "local-session")
        self.assertFalse(any(provider["key"] == "auth0" for provider in selection["providers"]))

    def test_failed_provider_validation_populates_last_failed_auth_operation(self) -> None:
        from backend.core.database import Base
        from backend.services import auth_provider_service, tenant_service
        from backend.services.exceptions import ServiceError

        engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
        Session = sessionmaker(bind=engine, expire_on_commit=False, future=True)
        Base.metadata.create_all(bind=engine)

        fake_settings = SimpleNamespace(
            demo_tenant_slug="alpha-desk",
            demo_tenant_name="Alpha Desk",
            demo_tenant_plan="enterprise",
            public_api_base_url="http://localhost:8010/api",
            auth_post_logout_redirect_url="http://localhost:5173/",
            auth0_allow_signup=True,
        )
        with (
            Session() as db,
            patch.object(tenant_service, "settings", fake_settings),
            patch.object(auth_provider_service, "settings", fake_settings),
            patch.object(
                auth_provider_service,
                "_fetch_json",
                side_effect=ServiceError("Auth provider request failed: boom", status_code=502),
            ),
        ):
            identity = tenant_service.ensure_demo_tenant_for_user(
                db,
                auth_subject="demo-trader",
                email="demo@example.test",
                name="Demo Trader",
                provider="local-demo",
                requested_tenant_slug="alpha-desk",
            )
            current_user = SimpleNamespace(
                tenant_id=identity["active_tenant"].id,
                tenant_slug="alpha-desk",
                tenant_name="Alpha Desk",
                tenant_plan="team",
                tenant_status="active",
                user_id="demo-trader",
                auth_subject="demo-trader",
                role="owner",
                platform_role="admin",
            )
            tenant_service.update_tenant_delivery_settings(
                db,
                current_user=current_user,
                updates={
                    "enabled_providers": ["auth0"],
                    "auth_policy": "prefer_sso",
                    "auth_provider_records": [
                        {
                            "provider_id": "provider_auth0_acme",
                            "provider_key": "auth0",
                            "label": "Acme Workforce",
                            "enabled": True,
                            "email_domains": ["acme.example.com"],
                            "auth0_domain": "acme.example.auth0.com",
                            "client_id": "tenant_client_123",
                            "client_secret": "tenant_secret_live",
                            "is_default": True,
                        }
                    ],
                },
            )
            snapshot = tenant_service.run_tenant_delivery_action(
                db,
                current_user=current_user,
                action="validate_auth_provider",
                provider_id="provider_auth0_acme",
            )

        record = snapshot["delivery"]["auth_routing"]["provider_records"][0]
        self.assertEqual(record["health_status"], "error")
        self.assertTrue(record["last_failed_at"])
        self.assertEqual(snapshot["delivery"]["auth_routing"]["recent_operations"][0]["status"], "error")
        self.assertTrue(snapshot["delivery"]["auth_routing"]["last_failed_at"])

    def test_activate_live_is_blocked_when_required_sso_is_not_launch_ready(self) -> None:
        from backend.core.database import Base
        from backend.services import tenant_service

        engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
        Session = sessionmaker(bind=engine, expire_on_commit=False, future=True)
        Base.metadata.create_all(bind=engine)

        fake_settings = SimpleNamespace(
            demo_tenant_slug="alpha-desk",
            demo_tenant_name="Alpha Desk",
            demo_tenant_plan="enterprise",
        )
        with (
            Session() as db,
            patch.object(tenant_service, "settings", fake_settings),
        ):
            identity = tenant_service.ensure_demo_tenant_for_user(
                db,
                auth_subject="demo-trader",
                email="demo@example.test",
                name="Demo Trader",
                provider="local-demo",
                requested_tenant_slug="alpha-desk",
            )
            current_user = SimpleNamespace(
                tenant_id=identity["active_tenant"].id,
                tenant_slug="alpha-desk",
                tenant_name="Alpha Desk",
                tenant_plan="team",
                tenant_status="active",
                user_id="demo-trader",
                auth_subject="demo-trader",
                role="owner",
                platform_role="admin",
            )
            tenant_service.update_tenant_delivery_settings(
                db,
                current_user=current_user,
                updates={
                    "primary_domain": "launch.alpha.example.com",
                    "domain_status": "verified",
                    "email_provider": "resend",
                    "provider_status": "ready",
                    "sender_name": "Alpha Desk",
                    "sender_email": "desk@launch.alpha.example.com",
                    "enabled_providers": ["local-session", "auth0"],
                    "auth_policy": "require_sso",
                    "preferred_provider": "auth0",
                    "auth_provider_records": [
                        {
                            "provider_id": "provider_auth0_acme",
                            "provider_key": "auth0",
                            "label": "Acme Workforce",
                            "enabled": True,
                            "email_domains": ["acme.example.com"],
                            "auth0_domain": "acme.example.auth0.com",
                            "client_id": "tenant_client_123",
                            "client_secret": "",
                            "is_default": True,
                        }
                    ],
                },
            )
            tenant_service.run_tenant_delivery_action(db, current_user=current_user, action="send_test")
            with self.assertRaises(ValidationError):
                tenant_service.run_tenant_delivery_action(db, current_user=current_user, action="activate_live")

    def test_support_snapshot_surfaces_white_label_launch_blockers(self) -> None:
        from backend.core.database import Base
        from backend.services import tenant_service

        engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
        Session = sessionmaker(bind=engine, expire_on_commit=False, future=True)
        Base.metadata.create_all(bind=engine)

        fake_settings = SimpleNamespace(
            demo_tenant_slug="alpha-desk",
            demo_tenant_name="Alpha Desk",
            demo_tenant_plan="enterprise",
        )
        with (
            Session() as db,
            patch.object(tenant_service, "settings", fake_settings),
        ):
            identity = tenant_service.ensure_demo_tenant_for_user(
                db,
                auth_subject="demo-trader",
                email="demo@example.test",
                name="Demo Trader",
                provider="local-demo",
                requested_tenant_slug="alpha-desk",
            )
            current_user = SimpleNamespace(
                tenant_id=identity["active_tenant"].id,
                tenant_slug="alpha-desk",
                tenant_name="Alpha Desk",
                tenant_plan="team",
                tenant_status="active",
                user_id="demo-trader",
                auth_subject="demo-trader",
                role="owner",
                platform_role="admin",
            )
            tenant_service.update_tenant_delivery_settings(
                db,
                current_user=current_user,
                updates={
                    "primary_domain": "launch.alpha.example.com",
                    "domain_status": "verified",
                    "email_provider": "resend",
                    "provider_status": "ready",
                    "sender_name": "Alpha Desk",
                    "sender_email": "desk@launch.alpha.example.com",
                    "enabled_providers": ["local-session", "auth0"],
                    "auth_policy": "require_sso",
                    "preferred_provider": "auth0",
                    "release_channel": "pilot",
                    "auth_provider_records": [
                        {
                            "provider_id": "provider_auth0_acme",
                            "provider_key": "auth0",
                            "label": "Acme Workforce",
                            "enabled": True,
                            "email_domains": ["acme.example.com"],
                            "auth0_domain": "acme.example.auth0.com",
                            "client_id": "tenant_client_123",
                            "client_secret": "",
                            "is_default": True,
                        }
                    ],
                },
            )
            tenant_service.run_tenant_delivery_action(db, current_user=current_user, action="send_test")
            tenant_service.update_tenant_status(db, current_user=current_user, status="paused")
            support = tenant_service.get_tenant_support_snapshot(db, current_user=current_user, limit=10)

        self.assertTrue(support["launch_ops"]["enabled"])
        self.assertFalse(support["launch_ops"]["launch_ready"])
        self.assertEqual(support["launch_ops"]["stage"], "Launch blocked")
        self.assertTrue(support["launch_ops"]["blockers"])
        self.assertFalse(support["support_actions"]["can_resume"])
        self.assertTrue(support["support_actions"]["resume_blockers"])

    def test_tenant_launch_rollup_surfaces_white_label_launch_blockers(self) -> None:
        from backend.core.database import Base
        from backend.services import tenant_service

        engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
        Session = sessionmaker(bind=engine, expire_on_commit=False, future=True)
        Base.metadata.create_all(bind=engine)

        fake_settings = SimpleNamespace(
            demo_tenant_slug="alpha-desk",
            demo_tenant_name="Alpha Desk",
            demo_tenant_plan="enterprise",
        )
        with (
            Session() as db,
            patch.object(tenant_service, "settings", fake_settings),
        ):
            identity = tenant_service.ensure_demo_tenant_for_user(
                db,
                auth_subject="demo-trader",
                email="demo@example.test",
                name="Demo Trader",
                provider="local-demo",
                requested_tenant_slug="alpha-desk",
            )
            current_user = SimpleNamespace(
                tenant_id=identity["active_tenant"].id,
                tenant_slug="alpha-desk",
                tenant_name="Alpha Desk",
                tenant_plan="team",
                tenant_status="active",
                user_id="demo-trader",
                auth_subject="demo-trader",
                role="owner",
                platform_role="admin",
            )
            tenant_service.update_tenant_delivery_settings(
                db,
                current_user=current_user,
                updates={
                    "primary_domain": "launch.alpha.example.com",
                    "domain_status": "verified",
                    "email_provider": "resend",
                    "provider_status": "ready",
                    "sender_name": "Alpha Desk",
                    "sender_email": "desk@launch.alpha.example.com",
                    "enabled_providers": ["local-session", "auth0"],
                    "auth_policy": "require_sso",
                    "preferred_provider": "auth0",
                    "release_channel": "pilot",
                    "auth_provider_records": [
                        {
                            "provider_id": "provider_auth0_acme",
                            "provider_key": "auth0",
                            "label": "Acme Workforce",
                            "enabled": True,
                            "email_domains": ["acme.example.com"],
                            "auth0_domain": "acme.example.auth0.com",
                            "client_id": "tenant_client_123",
                            "client_secret": "",
                            "is_default": True,
                        }
                    ],
                },
            )
            tenant_service.run_tenant_delivery_action(db, current_user=current_user, action="send_test")
            rollup = tenant_service.get_tenant_launch_rollup(db=db, tenant_slug="alpha-desk")

        self.assertEqual(rollup["tenant"]["slug"], "systematic-equities")
        self.assertEqual(rollup["summary"]["status"], "blocked")
        self.assertEqual(rollup["summary"]["stage"], "Launch blocked")
        self.assertFalse(rollup["summary"]["launch_ready"])
        self.assertGreaterEqual(rollup["summary"]["blocker_count"], 1)
        self.assertEqual(rollup["summary"]["release_channel"], "pilot")
        self.assertTrue(any(item["key"] == "auth_routing" and not item["complete"] for item in rollup["checklist"]))
        self.assertTrue(rollup["checks"]["domain_required"])
        self.assertTrue(rollup["checks"]["sender_required"])
        self.assertTrue(rollup["checks"]["auth_required"])
        self.assertFalse(rollup["checks"]["auth_ready"])
        self.assertTrue(rollup["blockers"])

    def test_resume_active_is_blocked_when_white_label_launch_ops_are_unready(self) -> None:
        from backend.core.database import Base
        from backend.services import tenant_service

        engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
        Session = sessionmaker(bind=engine, expire_on_commit=False, future=True)
        Base.metadata.create_all(bind=engine)

        fake_settings = SimpleNamespace(
            demo_tenant_slug="alpha-desk",
            demo_tenant_name="Alpha Desk",
            demo_tenant_plan="enterprise",
        )
        with (
            Session() as db,
            patch.object(tenant_service, "settings", fake_settings),
        ):
            identity = tenant_service.ensure_demo_tenant_for_user(
                db,
                auth_subject="demo-trader",
                email="demo@example.test",
                name="Demo Trader",
                provider="local-demo",
                requested_tenant_slug="alpha-desk",
            )
            current_user = SimpleNamespace(
                tenant_id=identity["active_tenant"].id,
                tenant_slug="alpha-desk",
                tenant_name="Alpha Desk",
                tenant_plan="team",
                tenant_status="active",
                user_id="demo-trader",
                auth_subject="demo-trader",
                role="owner",
                platform_role="admin",
            )
            tenant_service.update_tenant_delivery_settings(
                db,
                current_user=current_user,
                updates={
                    "primary_domain": "launch.alpha.example.com",
                    "domain_status": "verified",
                    "email_provider": "resend",
                    "provider_status": "ready",
                    "sender_name": "Alpha Desk",
                    "sender_email": "desk@launch.alpha.example.com",
                    "release_channel": "pilot",
                },
            )
            tenant_service.update_tenant_status(db, current_user=current_user, status="paused")
            with self.assertRaises(ValidationError):
                tenant_service.update_tenant_status(db, current_user=current_user, status="active")

    def test_launch_ready_event_is_audited_and_sent_to_partner_webhooks(self) -> None:
        from backend.core.database import Base
        from backend.services import tenant_service, workspace_service

        engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
        Session = sessionmaker(bind=engine, expire_on_commit=False, future=True)
        Base.metadata.create_all(bind=engine)

        fake_settings = SimpleNamespace(
            demo_tenant_slug="alpha-desk",
            demo_tenant_name="Alpha Desk",
            demo_tenant_plan="enterprise",
            api_token_salt="test-token-salt",
            partner_webhook_timeout_seconds=1,
            job_worker_batch_size=12,
        )
        workspace_path = Path("tests") / "_tmp_launch_ready_workspaces.json"
        legacy_path = Path("tests") / "_tmp_launch_ready_legacy_workspaces.json"
        response = MagicMock()
        response.status = 200
        response_context = MagicMock()
        response_context.__enter__.return_value = response
        response_context.__exit__.return_value = False
        try:
            with (
                Session() as db,
                patch.object(tenant_service, "settings", fake_settings),
                patch.object(workspace_service, "_WORKSPACE_FILE", workspace_path),
                patch.object(workspace_service, "_LEGACY_WORKSPACE_FILE", legacy_path),
                patch.object(tenant_service.urlrequest, "urlopen", return_value=response_context),
            ):
                identity = tenant_service.ensure_demo_tenant_for_user(
                    db,
                    auth_subject="demo-trader",
                    email="demo@example.test",
                    name="Demo Trader",
                    provider="local-demo",
                    requested_tenant_slug="alpha-desk",
                )
                current_user = SimpleNamespace(
                    tenant_id=identity["active_tenant"].id,
                    tenant_slug="alpha-desk",
                    user_id="demo-trader",
                    auth_subject="demo-trader",
                    role="owner",
                    platform_role="admin",
                )
                tenant_service.create_tenant_partner_webhook(
                    db,
                    current_user=current_user,
                    name="Launch listener",
                    url="https://example.test/webhook",
                    events=["tenant.launch_ready"],
                )
                tenant_service.update_tenant_branding(
                    db,
                    current_user=current_user,
                    updates={
                        "app_name": "Pilot Console",
                        "support_email": "support@example.test",
                    },
                )
                tenant_service.seed_tenant_onboarding_workspace(db, current_user=current_user)
                tenant_service.update_tenant_onboarding_step(
                    db,
                    current_user=current_user,
                    step_key="launch_review",
                    completed=True,
                )
                tenant_service.update_tenant_delivery_settings(
                    db,
                    current_user=current_user,
                    updates={
                        "primary_domain": "launch.alpha.example.com",
                        "domain_status": "verified",
                        "email_provider": "resend",
                        "provider_status": "ready",
                        "sender_name": "Alpha Desk",
                        "sender_email": "desk@launch.alpha.example.com",
                    },
                )
                tenant_service.run_tenant_delivery_action(db, current_user=current_user, action="send_test")
                tenant_service.run_tenant_delivery_action(db, current_user=current_user, action="activate_live")
                support = tenant_service.get_tenant_support_snapshot(db, current_user=current_user, limit=20)
                webhooks = tenant_service.get_tenant_partner_webhooks(db, current_user=current_user)

            event_types = [item["event_type"] for item in support["timeline"]["items"]]
            self.assertIn("tenant.launch_ready", event_types)
            self.assertTrue(support["launch_ops"]["launch_ready"])
            self.assertEqual(webhooks["webhooks"]["deliveries"][0]["event_key"], "tenant.launch_ready")
            self.assertEqual(webhooks["webhooks"]["items"][0]["last_delivery_status"], "success")
        finally:
            if workspace_path.exists():
                workspace_path.unlink()
            if legacy_path.exists():
                legacy_path.unlink()

    def test_workspace_saved_event_is_audited_and_sent_to_partner_webhooks(self) -> None:
        from backend.core.database import Base
        from backend.routers import system as system_router
        from backend.schemas import SaveWorkspaceRequest
        from backend.services import tenant_service, workspace_service

        engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
        Session = sessionmaker(bind=engine, expire_on_commit=False, future=True)
        Base.metadata.create_all(bind=engine)

        fake_settings = SimpleNamespace(
            demo_tenant_slug="alpha-desk",
            demo_tenant_name="Alpha Desk",
            demo_tenant_plan="team",
            api_token_salt="test-token-salt",
            partner_webhook_timeout_seconds=1,
            job_worker_batch_size=12,
        )
        workspace_path = Path("tests") / "_tmp_workspace_event_workspaces.json"
        legacy_path = Path("tests") / "_tmp_workspace_event_legacy_workspaces.json"
        response = MagicMock()
        response.status = 200
        response_context = MagicMock()
        response_context.__enter__.return_value = response
        response_context.__exit__.return_value = False
        try:
            with (
                Session() as db,
                patch.object(tenant_service, "settings", fake_settings),
                patch.object(workspace_service, "_WORKSPACE_FILE", workspace_path),
                patch.object(workspace_service, "_LEGACY_WORKSPACE_FILE", legacy_path),
                patch.object(tenant_service.urlrequest, "urlopen", return_value=response_context),
            ):
                identity = tenant_service.ensure_demo_tenant_for_user(
                    db,
                    auth_subject="demo-trader",
                    email="demo@example.test",
                    name="Demo Trader",
                    provider="local-demo",
                    requested_tenant_slug="alpha-desk",
                )
                current_user = SimpleNamespace(
                    tenant_id=identity["active_tenant"].id,
                    tenant_slug="alpha-desk",
                    user_id="demo-trader",
                    auth_subject="demo-trader",
                    role="owner",
                    platform_role="admin",
                )
                tenant_service.create_tenant_partner_webhook(
                    db,
                    current_user=current_user,
                    name="Workspace listener",
                    url="https://example.test/webhook",
                    events=["workspace.saved"],
                )
                saved = system_router.frontend_save_workspace(
                    SaveWorkspaceRequest(
                        name="Launch Desk",
                        page="dashboard",
                        payload={"ticker": "SPY"},
                        notes="Seeded during tests",
                        pinned=True,
                        tags=["launch"],
                    ),
                    current_user=current_user,
                    db=db,
                )
                support = tenant_service.get_tenant_support_snapshot(db, current_user=current_user, limit=20)
                webhooks = tenant_service.get_tenant_partner_webhooks(db, current_user=current_user)

            event_types = [item["event_type"] for item in support["timeline"]["items"]]
            self.assertEqual(saved.data["name"], "Launch Desk")
            self.assertIn("workspace.saved", event_types)
            self.assertEqual(webhooks["webhooks"]["deliveries"][0]["event_key"], "workspace.saved")
            self.assertEqual(webhooks["webhooks"]["items"][0]["last_delivery_status"], "success")
        finally:
            if workspace_path.exists():
                workspace_path.unlink()
            if legacy_path.exists():
                legacy_path.unlink()

    def test_billing_plan_changed_event_is_audited_and_sent_to_partner_webhooks(self) -> None:
        from backend.core.database import Base
        from backend.services import billing_service, tenant_service

        engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
        Session = sessionmaker(bind=engine, expire_on_commit=False, future=True)
        Base.metadata.create_all(bind=engine)

        fake_settings = SimpleNamespace(
            demo_tenant_slug="alpha-desk",
            demo_tenant_name="Alpha Desk",
            demo_tenant_plan="team",
            api_token_salt="test-token-salt",
            partner_webhook_timeout_seconds=1,
            job_worker_batch_size=12,
        )
        response = MagicMock()
        response.status = 200
        response_context = MagicMock()
        response_context.__enter__.return_value = response
        response_context.__exit__.return_value = False
        with (
            Session() as db,
            patch.object(tenant_service, "settings", fake_settings),
            patch.object(tenant_service.urlrequest, "urlopen", return_value=response_context),
        ):
            identity = tenant_service.ensure_demo_tenant_for_user(
                db,
                auth_subject="demo-trader",
                email="demo@example.test",
                name="Demo Trader",
                provider="local-demo",
                requested_tenant_slug="alpha-desk",
            )
            current_user = SimpleNamespace(
                tenant_id=identity["active_tenant"].id,
                tenant_slug="alpha-desk",
                user_id="demo-trader",
                auth_subject="demo-trader",
                role="owner",
                platform_role="admin",
            )
            tenant_service.create_tenant_partner_webhook(
                db,
                current_user=current_user,
                name="Billing listener",
                url="https://example.test/webhook",
                events=["billing.plan_changed"],
            )
            summary = billing_service.change_tenant_plan(db, current_user, "enterprise")
            support = tenant_service.get_tenant_support_snapshot(db, current_user=current_user, limit=20)
            webhooks = tenant_service.get_tenant_partner_webhooks(db, current_user=current_user)

        event_types = [item["event_type"] for item in support["timeline"]["items"]]
        self.assertEqual(summary["plan"]["key"], "enterprise")
        self.assertIn("billing.plan_changed", event_types)
        self.assertEqual(webhooks["webhooks"]["deliveries"][0]["event_key"], "billing.plan_changed")
        self.assertEqual(webhooks["webhooks"]["items"][0]["last_delivery_status"], "success")

    def test_market_chart_event_is_audited_and_sent_to_partner_webhooks(self) -> None:
        from backend.core.database import Base
        from backend.routers import market as market_router
        from backend.services import tenant_service

        engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
        Session = sessionmaker(bind=engine, expire_on_commit=False, future=True)
        Base.metadata.create_all(bind=engine)

        fake_settings = SimpleNamespace(
            demo_tenant_slug="alpha-desk",
            demo_tenant_name="Alpha Desk",
            demo_tenant_plan="team",
            api_token_salt="test-token-salt",
            partner_webhook_timeout_seconds=1,
            job_worker_batch_size=12,
        )
        response = MagicMock()
        response.status = 200
        response_context = MagicMock()
        response_context.__enter__.return_value = response
        response_context.__exit__.return_value = False
        with (
            Session() as db,
            patch.object(tenant_service, "settings", fake_settings),
            patch.object(tenant_service.urlrequest, "urlopen", return_value=response_context),
            patch.object(
                market_router,
                "get_chart_payload",
                return_value={
                    "ticker": "SPY",
                    "interval": "5m",
                    "point_count": 3,
                    "candles": [
                        {"datetime": "2026-04-16T13:30:00+00:00", "open": 700.0, "high": 701.0, "low": 699.5, "close": 700.5, "volume": 1000},
                    ],
                    "overlays": {},
                    "available_indicators": ["ema_9"],
                    "strategy": {"available": True},
                },
            ),
        ):
            identity = tenant_service.ensure_demo_tenant_for_user(
                db,
                auth_subject="demo-trader",
                email="demo@example.test",
                name="Demo Trader",
                provider="local-demo",
                requested_tenant_slug="alpha-desk",
            )
            current_user = SimpleNamespace(
                tenant_id=identity["active_tenant"].id,
                tenant_slug="alpha-desk",
                tenant_name="Alpha Desk",
                tenant_plan="team",
                tenant_status="active",
                user_id="demo-trader",
                auth_subject="demo-trader",
                role="owner",
                platform_role="admin",
            )
            tenant_service.create_tenant_partner_webhook(
                db,
                current_user=current_user,
                name="Market listener",
                url="https://example.test/webhook",
                events=["market.signal_ready"],
            )
            result = market_router.chart_payload(
                "SPY",
                interval="5m",
                points_limit=120,
                background_tasks=None,
                current_user=current_user,
                db=db,
            )
            support = tenant_service.get_tenant_support_snapshot(db, current_user=current_user, limit=20)
            webhooks = tenant_service.get_tenant_partner_webhooks(db, current_user=current_user)

        event_types = [item["event_type"] for item in support["timeline"]["items"]]
        self.assertEqual(result.data["ticker"], "SPY")
        self.assertIn("market.signal_ready", event_types)
        self.assertEqual(webhooks["webhooks"]["deliveries"][0]["event_key"], "market.signal_ready")
        self.assertEqual(webhooks["webhooks"]["items"][0]["last_delivery_status"], "success")

    def test_market_scan_event_is_audited_and_sent_to_partner_webhooks(self) -> None:
        from backend.core.database import Base
        from backend.routers import market as market_router
        from backend.schemas import ScanRequest
        from backend.services import tenant_service

        engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
        Session = sessionmaker(bind=engine, expire_on_commit=False, future=True)
        Base.metadata.create_all(bind=engine)

        fake_settings = SimpleNamespace(
            demo_tenant_slug="alpha-desk",
            demo_tenant_name="Alpha Desk",
            demo_tenant_plan="team",
            api_token_salt="test-token-salt",
            partner_webhook_timeout_seconds=1,
            job_worker_batch_size=12,
        )
        response = MagicMock()
        response.status = 200
        response_context = MagicMock()
        response_context.__enter__.return_value = response
        response_context.__exit__.return_value = False
        with (
            Session() as db,
            patch.object(tenant_service, "settings", fake_settings),
            patch.object(tenant_service.urlrequest, "urlopen", return_value=response_context),
            patch.object(
                market_router,
                "run_scan",
                return_value={
                    "results": [
                        {"ticker": "SPY", "trade_decision": "ENTER NOW", "direction": "CALL", "trade_status": "ENTER NOW"},
                        {"ticker": "QQQ", "trade_decision": "WATCH", "direction": "PUT", "trade_status": "WAIT"},
                    ],
                    "count": 2,
                    "errors": [],
                },
            ),
        ):
            identity = tenant_service.ensure_demo_tenant_for_user(
                db,
                auth_subject="demo-trader",
                email="demo@example.test",
                name="Demo Trader",
                provider="local-demo",
                requested_tenant_slug="alpha-desk",
            )
            current_user = SimpleNamespace(
                tenant_id=identity["active_tenant"].id,
                tenant_slug="alpha-desk",
                tenant_name="Alpha Desk",
                tenant_plan="team",
                tenant_status="active",
                user_id="demo-trader",
                auth_subject="demo-trader",
                role="owner",
                platform_role="admin",
            )
            tenant_service.create_tenant_partner_webhook(
                db,
                current_user=current_user,
                name="Scan listener",
                url="https://example.test/webhook",
                events=["market.signal_ready"],
            )
            result = market_router.scan(
                ScanRequest(tickers=["SPY", "QQQ"], interval="5m", horizon=5, top_n=2),
                background_tasks=None,
                current_user=current_user,
                db=db,
            )
            support = tenant_service.get_tenant_support_snapshot(db, current_user=current_user, limit=20)
            webhooks = tenant_service.get_tenant_partner_webhooks(db, current_user=current_user)

        event_types = [item["event_type"] for item in support["timeline"]["items"]]
        self.assertEqual(len(result.data["results"]), 2)
        self.assertIn("market.signal_ready", event_types)
        self.assertEqual(webhooks["webhooks"]["deliveries"][0]["event_key"], "market.signal_ready")
        self.assertEqual(webhooks["webhooks"]["items"][0]["last_delivery_status"], "success")

    def test_market_analyze_local_demo_preserves_option_contract_without_plan_entitlement(self) -> None:
        from backend.routers import market as market_router
        from backend.schemas import AnalyzeRequest

        current_user = SimpleNamespace(
            provider="local-demo",
            tenant_slug="alpha-desk",
            tenant_name="Alpha Desk",
            tenant_plan="starter",
            tenant_status="active",
        )
        contract = {
            "contract_symbol": "MSFT260515P00400000",
            "expiration": "2026-05-15",
            "strike": 400.0,
            "bid": 7.2,
            "ask": 7.5,
            "mid": 7.35,
            "spread_pct": 0.0408,
        }
        payload = {
            "ticker": "MSFT",
            "report": {
                "option_plan": {
                    "action": "BUY PUT",
                    "recommended_contract": contract,
                },
            },
        }
        with (
            patch.object(market_router, "analyze_market", return_value=payload),
            patch.object(market_router, "has_entitlement", return_value=False),
            patch.object(market_router, "_queue_market_signal_ready_event"),
        ):
            result = market_router.analyze(
                AnalyzeRequest(ticker="MSFT", interval="5m", horizon=5, instrument_type="listed_option", include_contract_lookup=True),
                background_tasks=None,
                current_user=current_user,
                db=MagicMock(),
            )

        self.assertEqual(result.data["report"]["option_plan"]["recommended_contract"]["contract_symbol"], contract["contract_symbol"])
        self.assertTrue(result.data["capabilities"]["broker_execution"])

    def test_token_permission_resolution_is_scope_bound(self) -> None:
        from backend.services.permissions import permission_map_from_permissions

        permissions = _resolved_permissions(
            membership_role="viewer",
            platform_role="member",
            mode="token",
            scopes=("tenant.read", "market.read"),
        )
        permission_map = permission_map_from_permissions(permissions)

        self.assertTrue(permission_map["tenant.read"])
        self.assertTrue(permission_map["market.read"])
        self.assertFalse(permission_map.get("tenant.manage_branding", False))
        self.assertFalse(permission_map.get("tenant.create", False))

    def test_workspaces_are_scoped_by_user(self) -> None:
        from backend.services import workspace_service

        workspace_path = Path("tests") / "_tmp_workspaces.json"
        legacy_path = Path("tests") / "_tmp_legacy_workspaces.json"
        try:
            with (
                patch.object(workspace_service, "_WORKSPACE_FILE", workspace_path),
                patch.object(workspace_service, "_LEGACY_WORKSPACE_FILE", legacy_path),
            ):
                workspace_service.save_workspace("user-a", "Desk", "dashboard", {"ticker": "SPY"})
                workspace_service.save_workspace("user-b", "Desk", "dashboard", {"ticker": "QQQ"})

                user_a = workspace_service.list_workspaces("user-a")
                user_b = workspace_service.list_workspaces("user-b")

            self.assertEqual(user_a["count"], 1)
            self.assertEqual(user_b["count"], 1)
            self.assertEqual(user_a["items"][0]["payload"]["ticker"], "SPY")
            self.assertEqual(user_b["items"][0]["payload"]["ticker"], "QQQ")
        finally:
            if workspace_path.exists():
                workspace_path.unlink()
            if legacy_path.exists():
                legacy_path.unlink()

    def test_workspaces_are_scoped_by_user_and_tenant(self) -> None:
        from backend.services import workspace_service

        workspace_path = Path("tests") / "_tmp_tenant_workspaces.json"
        legacy_path = Path("tests") / "_tmp_tenant_legacy_workspaces.json"
        try:
            with (
                patch.object(workspace_service, "_WORKSPACE_FILE", workspace_path),
                patch.object(workspace_service, "_LEGACY_WORKSPACE_FILE", legacy_path),
            ):
                workspace_service.save_workspace("user-a", "Desk", "dashboard", {"ticker": "SPY"}, tenant_slug="alpha-desk")
                workspace_service.save_workspace("user-a", "Desk", "dashboard", {"ticker": "QQQ"}, tenant_slug="pilot-fund")

                alpha = workspace_service.list_workspaces("user-a", tenant_slug="alpha-desk")
                pilot = workspace_service.list_workspaces("user-a", tenant_slug="pilot-fund")

            self.assertEqual(alpha["count"], 1)
            self.assertEqual(pilot["count"], 1)
            self.assertEqual(alpha["items"][0]["payload"]["ticker"], "SPY")
            self.assertEqual(pilot["items"][0]["payload"]["ticker"], "QQQ")
        finally:
            if workspace_path.exists():
                workspace_path.unlink()
            if legacy_path.exists():
                legacy_path.unlink()

    def test_trade_journal_validation_snapshot_includes_saved_board_history(self) -> None:
        from backend.services import portfolio_service, workspace_service

        journal = pd.DataFrame(
            [
                {
                    "ticker": "SPY",
                    "pnl_dollars": 120.0,
                    "setup_score": 82.5,
                    "event_risk": False,
                    "execution_review_key": "clean_fill",
                    "execution_review_label": "Clean fill",
                    "execution_review_detail": "Filled inside the expected spread window.",
                    "fill_slippage_bps": 2.4,
                    "fill_slippage_dollars": 0.02,
                    "expected_fill_price": 2.50,
                    "actual_fill_price": 2.52,
                    "probability_up": 0.64,
                    "average_probability_up": 0.55,
                    "attribution_key": "clean_win",
                    "closed_at": "2099-04-18T15:30:00+00:00",
                },
                {
                    "ticker": "NVDA",
                    "pnl_dollars": -45.0,
                    "setup_score": 68.0,
                    "event_risk": True,
                    "execution_review_key": "fragile_fill",
                    "execution_review_label": "Fragile fill",
                    "execution_review_detail": "Spread widened into the catalyst window.",
                    "fill_slippage_bps": 12.8,
                    "fill_slippage_dollars": 0.09,
                    "expected_fill_price": 3.10,
                    "actual_fill_price": 3.19,
                    "probability_up": 0.52,
                    "average_probability_up": 0.56,
                    "attribution_key": "execution_drift",
                    "closed_at": "2099-04-17T15:30:00+00:00",
                },
            ]
        )
        validation_artifact = {
            "artifact_type": "candidate_board_snapshot",
            "board_name": "Controlled liquid ranking board",
            "source": "watchlist",
            "interval": "5m",
            "horizon": 5,
            "leader": {
                "ticker": "SPY",
                "ranking_score": 84.2,
                "ranking_label": "Promote first",
            },
            "summary": {
                "candidate_count": 6,
                "promote_count": 2,
                "review_count": 3,
                "stand_down_count": 1,
                "event_window_count": 1,
                "fragile_execution_count": 1,
            },
        }

        with _workspace_tempdir() as temp_dir:
            workspace_path = Path(temp_dir) / "validation_workspaces.json"
            legacy_path = Path(temp_dir) / "validation_legacy_workspaces.json"
            with (
                patch.object(workspace_service, "_WORKSPACE_FILE", workspace_path),
                patch.object(workspace_service, "_LEGACY_WORKSPACE_FILE", legacy_path),
                patch.object(portfolio_service, "_load_trade_journal_frame", return_value=journal),
                patch.object(portfolio_service.sdm, "build_trade_replay", return_value=pd.DataFrame()),
            ):
                workspace_service.save_workspace(
                    "demo-trader",
                    "Validation replay",
                    "watchlist",
                    {"validation_artifact": validation_artifact},
                    tenant_slug="alpha-desk",
                )

                payload = portfolio_service.get_trade_journal(
                    limit=25,
                    offset=0,
                    current_user=SimpleNamespace(user_id="demo-trader", tenant_slug="alpha-desk"),
                )

        snapshot = payload["validation_snapshot"]
        self.assertEqual(snapshot["board_snapshot_history"]["count"], 1)
        self.assertEqual(snapshot["board_snapshot_history"]["items"][0]["leader_ticker"], "SPY")
        self.assertTrue(any(item["key"] == "ranking_board" for item in snapshot["scorecards"]))
        self.assertEqual(snapshot["route_quality"]["clean_fill_count"], 1)
        self.assertEqual(snapshot["route_quality"]["fragile_fill_count"], 1)
        self.assertEqual(snapshot["replay_comparisons"]["board_outcomes"]["resolved_count"], 1)
        self.assertEqual(snapshot["replay_comparisons"]["board_outcomes"]["items"][0]["leader_ticker"], "SPY")
        self.assertEqual(snapshot["replay_comparisons"]["paper_live_slippage"]["count"], 2)
        self.assertIsNotNone(snapshot["replay_comparisons"]["paper_live_slippage"]["average_abs_slippage_bps"])

    def test_demo_tenant_seed_creates_membership_and_active_tenant(self) -> None:
        from backend.core.database import Base
        from backend.services import tenant_service

        engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
        Session = sessionmaker(bind=engine, expire_on_commit=False, future=True)
        Base.metadata.create_all(bind=engine)

        fake_settings = SimpleNamespace(
            demo_tenant_slug="alpha-desk",
            demo_tenant_name="Alpha Desk",
            demo_tenant_plan="pro",
        )
        with (
            Session() as db,
            patch.object(tenant_service, "settings", fake_settings),
        ):
            payload = tenant_service.ensure_demo_tenant_for_user(
                db,
                auth_subject="demo-trader",
                email="demo@example.test",
                name="Demo Trader",
                provider="local-demo",
                requested_tenant_slug="alpha-desk",
            )

        membership_slugs = sorted(item.tenant.slug for item in payload["memberships"])

        self.assertEqual(payload["active_tenant"].slug, "systematic-equities")
        self.assertEqual(payload["active_tenant"].plan_key, "pro")
        self.assertEqual(membership_slugs, ["macro", "stat-arb", "systematic-equities"])
        self.assertTrue(all(item.role == "owner" for item in payload["memberships"]))
        self.assertEqual(
            sorted(item["tenant"]["slug"] for item in payload["memberships_payload"]),
            ["macro", "stat-arb", "systematic-equities"],
        )

    def test_tenant_branding_update_persists_and_reaches_session_payload(self) -> None:
        from backend.core import auth as auth_core
        from backend.core.database import Base
        from backend.services import auth_service, tenant_service

        engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
        Session = sessionmaker(bind=engine, expire_on_commit=False, future=True)
        Base.metadata.create_all(bind=engine)

        fake_settings = SimpleNamespace(
            demo_tenant_slug="alpha-desk",
            demo_tenant_name="Alpha Desk",
            demo_tenant_plan="pro",
            auth_provider="local-demo",
            environment="development",
            demo_user_id="demo-trader",
            demo_user_email="demo@example.test",
            demo_user_name="Demo Trader",
        )
        with (
            Session() as db,
            patch.object(auth_core, "settings", fake_settings),
            patch.object(tenant_service, "settings", fake_settings),
        ):
            identity = tenant_service.ensure_demo_tenant_for_user(
                db,
                auth_subject="demo-trader",
                email="demo@example.test",
                name="Demo Trader",
                provider="local-demo",
                requested_tenant_slug="alpha-desk",
            )
            tenant_service.update_tenant_branding(
                db,
                current_user=SimpleNamespace(
                    tenant_id=identity["active_tenant"].id,
                    role="owner",
                    platform_role="admin",
                ),
                updates={
                    "app_name": "Pilot Console",
                    "app_tagline": "White-label trading workspace",
                    "accent_primary": "#123456",
                    "logo_url": "https://example.test/logo.svg",
                },
            )
            session_payload = auth_service.build_session_payload(auth_core.build_demo_user(db=db))

        self.assertEqual(session_payload["active_tenant"]["brand_settings"]["app_name"], "Pilot Console")
        self.assertEqual(session_payload["active_tenant"]["brand_settings"]["accent_primary"], "#123456")
        self.assertEqual(session_payload["active_tenant"]["logo_url"], "https://example.test/logo.svg")

    def test_tenant_branding_requires_plan_entitlement(self) -> None:
        from backend.core.database import Base
        from backend.services import tenant_service

        engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
        Session = sessionmaker(bind=engine, expire_on_commit=False, future=True)
        Base.metadata.create_all(bind=engine)

        fake_settings = SimpleNamespace(
            demo_tenant_slug="alpha-desk",
            demo_tenant_name="Alpha Desk",
            demo_tenant_plan="starter",
        )
        with (
            Session() as db,
            patch.object(tenant_service, "settings", fake_settings),
        ):
            identity = tenant_service.ensure_demo_tenant_for_user(
                db,
                auth_subject="demo-trader",
                email="demo@example.test",
                name="Demo Trader",
                provider="local-demo",
                requested_tenant_slug="alpha-desk",
            )
            with self.assertRaises(ForbiddenError):
                tenant_service.update_tenant_branding(
                    db,
                    current_user=SimpleNamespace(
                        tenant_id=identity["active_tenant"].id,
                        role="owner",
                        platform_role="admin",
                    ),
                    updates={"app_name": "Starter White Label"},
                )

    def test_tenant_branding_requires_manage_branding_permission(self) -> None:
        from backend.core.database import Base
        from backend.services import tenant_service

        engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
        Session = sessionmaker(bind=engine, expire_on_commit=False, future=True)
        Base.metadata.create_all(bind=engine)

        fake_settings = SimpleNamespace(
            demo_tenant_slug="alpha-desk",
            demo_tenant_name="Alpha Desk",
            demo_tenant_plan="pro",
        )
        with (
            Session() as db,
            patch.object(tenant_service, "settings", fake_settings),
        ):
            identity = tenant_service.ensure_demo_tenant_for_user(
                db,
                auth_subject="demo-trader",
                email="demo@example.test",
                name="Demo Trader",
                provider="local-demo",
                requested_tenant_slug="alpha-desk",
            )
            with self.assertRaises(ForbiddenError):
                tenant_service.update_tenant_branding(
                    db,
                    current_user=SimpleNamespace(
                        tenant_id=identity["active_tenant"].id,
                        role="viewer",
                        platform_role="member",
                        permissions=_resolved_permissions(membership_role="viewer", platform_role="member"),
                    ),
                    updates={"app_name": "Viewer Should Not Save"},
                )

    def test_onboarding_snapshot_and_seeded_workspace_progress(self) -> None:
        from backend.core.database import Base
        from backend.services import tenant_service, workspace_service

        engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
        Session = sessionmaker(bind=engine, expire_on_commit=False, future=True)
        Base.metadata.create_all(bind=engine)

        workspace_path = Path("tests") / "_tmp_onboarding_workspaces.json"
        legacy_path = Path("tests") / "_tmp_onboarding_legacy_workspaces.json"
        fake_settings = SimpleNamespace(
            demo_tenant_slug="alpha-desk",
            demo_tenant_name="Alpha Desk",
            demo_tenant_plan="pro",
            demo_user_id="demo-trader",
        )
        try:
            with (
                Session() as db,
                patch.object(tenant_service, "settings", fake_settings),
                patch.object(workspace_service, "_WORKSPACE_FILE", workspace_path),
                patch.object(workspace_service, "_LEGACY_WORKSPACE_FILE", legacy_path),
            ):
                identity = tenant_service.ensure_demo_tenant_for_user(
                    db,
                    auth_subject="demo-trader",
                    email="demo@example.test",
                    name="Demo Trader",
                    provider="local-demo",
                    requested_tenant_slug="alpha-desk",
                )
                current_user = SimpleNamespace(
                    tenant_id=identity["active_tenant"].id,
                    tenant_slug="alpha-desk",
                    user_id="demo-trader",
                    auth_subject="demo-trader",
                    role="owner",
                    platform_role="admin",
                )
                before = tenant_service.get_tenant_onboarding_snapshot(db, current_user=current_user)
                seeded = tenant_service.seed_tenant_onboarding_workspace(db, current_user=current_user)
                tenant_service.update_tenant_onboarding_step(
                    db,
                    current_user=current_user,
                    step_key="launch_review",
                    completed=True,
                )
                after = tenant_service.get_tenant_onboarding_snapshot(db, current_user=current_user)

            self.assertEqual(before["workspace_count"], 0)
            self.assertEqual(seeded["workspace"]["name"], "Personal Launchpad")
            self.assertGreater(after["workspace_count"], 0)
            launch_review = next(item for item in after["steps"] if item["key"] == "launch_review")
            workspace_step = next(item for item in after["steps"] if item["key"] == "starter_workspace")
            self.assertTrue(launch_review["completed"])
            self.assertTrue(workspace_step["completed"])
        finally:
            if workspace_path.exists():
                workspace_path.unlink()
            if legacy_path.exists():
                legacy_path.unlink()

    def test_support_snapshot_includes_timeline_and_status_updates(self) -> None:
        from backend.core.database import Base
        from backend.services import tenant_service

        engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
        Session = sessionmaker(bind=engine, expire_on_commit=False, future=True)
        Base.metadata.create_all(bind=engine)

        fake_settings = SimpleNamespace(
            demo_tenant_slug="alpha-desk",
            demo_tenant_name="Alpha Desk",
            demo_tenant_plan="pro",
        )
        with (
            Session() as db,
            patch.object(tenant_service, "settings", fake_settings),
        ):
            identity = tenant_service.ensure_demo_tenant_for_user(
                db,
                auth_subject="demo-trader",
                email="demo@example.test",
                name="Demo Trader",
                provider="local-demo",
                requested_tenant_slug="alpha-desk",
            )
            current_user = SimpleNamespace(
                tenant_id=identity["active_tenant"].id,
                tenant_slug="alpha-desk",
                user_id="demo-trader",
                auth_subject="demo-trader",
                role="owner",
                platform_role="admin",
            )
            tenant_service.update_tenant_onboarding_step(
                db,
                current_user=current_user,
                step_key="launch_review",
                completed=True,
            )
            tenant_service.update_tenant_status(db, current_user=current_user, status="paused")
            snapshot = tenant_service.get_tenant_support_snapshot(db, current_user=current_user, limit=10)

        self.assertEqual(snapshot["status"], "paused")
        self.assertGreaterEqual(snapshot["timeline"]["count"], 2)
        event_types = [item["event_type"] for item in snapshot["timeline"]["items"]]
        self.assertIn("tenant.onboarding_step_updated", event_types)
        self.assertIn("tenant.status_updated", event_types)
        self.assertTrue(snapshot["support_actions"]["can_resume"])

    def test_member_invitation_is_claimed_when_matching_user_signs_in(self) -> None:
        from backend.core.database import Base
        from backend.services import tenant_service

        engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
        Session = sessionmaker(bind=engine, expire_on_commit=False, future=True)
        Base.metadata.create_all(bind=engine)

        fake_settings = SimpleNamespace(
            demo_tenant_slug="alpha-desk",
            demo_tenant_name="Alpha Desk",
            demo_tenant_plan="pro",
            api_token_salt="unit-test-salt",
            partner_webhook_timeout_seconds=1,
            job_worker_batch_size=12,
        )
        with (
            Session() as db,
            patch.object(tenant_service, "settings", fake_settings),
        ):
            identity = tenant_service.ensure_demo_tenant_for_user(
                db,
                auth_subject="demo-trader",
                email="demo@example.test",
                name="Demo Trader",
                provider="local-demo",
                requested_tenant_slug="alpha-desk",
            )
            owner_user = SimpleNamespace(
                tenant_id=identity["active_tenant"].id,
                tenant_slug="alpha-desk",
                user_id="demo-trader",
                auth_subject="demo-trader",
                role="owner",
                platform_role="admin",
                permissions=_resolved_permissions(membership_role="owner", platform_role="admin"),
            )
            tenant_service.create_tenant_member_invitation(
                db,
                current_user=owner_user,
                email="analyst@example.test",
                role="analyst",
                name="Analyst User",
                message="Join the pilot desk",
            )
            invited_user = tenant_service.ensure_user(
                db,
                auth_subject="analyst-user",
                email="analyst@example.test",
                name="Analyst User",
                provider="local-demo",
                platform_role="member",
            )
            memberships = tenant_service.list_user_memberships(db, invited_user)
            support = tenant_service.get_tenant_support_snapshot(db, current_user=owner_user)
            claimed_role = memberships[0].role
            claimed_tenant_slug = memberships[0].tenant.slug

        self.assertEqual(len(memberships), 1)
        self.assertEqual(claimed_role, "analyst")
        self.assertEqual(claimed_tenant_slug, "systematic-equities")
        invitation = next(item for item in support["invitations"]["items"] if item["email"] == "analyst@example.test")
        self.assertEqual(invitation["status"], "accepted")
        self.assertEqual(support["memberships"]["count"], 2)

    def test_member_role_update_and_removal_refresh_support_snapshot(self) -> None:
        from backend.core.database import Base
        from backend.services import tenant_service

        engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
        Session = sessionmaker(bind=engine, expire_on_commit=False, future=True)
        Base.metadata.create_all(bind=engine)

        fake_settings = SimpleNamespace(
            demo_tenant_slug="alpha-desk",
            demo_tenant_name="Alpha Desk",
            demo_tenant_plan="pro",
        )
        with (
            Session() as db,
            patch.object(tenant_service, "settings", fake_settings),
        ):
            identity = tenant_service.ensure_demo_tenant_for_user(
                db,
                auth_subject="demo-trader",
                email="demo@example.test",
                name="Demo Trader",
                provider="local-demo",
                requested_tenant_slug="alpha-desk",
            )
            owner_user = SimpleNamespace(
                tenant_id=identity["active_tenant"].id,
                tenant_slug="alpha-desk",
                user_id="demo-trader",
                auth_subject="demo-trader",
                role="owner",
                platform_role="admin",
                permissions=_resolved_permissions(membership_role="owner", platform_role="admin"),
            )
            tenant_service.create_tenant_member_invitation(
                db,
                current_user=owner_user,
                email="trader@example.test",
                role="viewer",
            )
            invited_user = tenant_service.ensure_user(
                db,
                auth_subject="trader-user",
                email="trader@example.test",
                name="Trader User",
                provider="local-demo",
                platform_role="member",
            )
            membership = tenant_service.list_user_memberships(db, invited_user)[0]
            updated = tenant_service.update_tenant_membership_role(
                db,
                current_user=owner_user,
                membership_id=membership.id,
                role="trader",
            )
            removed = tenant_service.remove_tenant_membership(
                db,
                current_user=owner_user,
                membership_id=membership.id,
            )

        updated_member = next(item for item in updated["support"]["memberships"]["items"] if item["email"] == "trader@example.test")
        self.assertEqual(updated_member["role"], "trader")
        self.assertEqual(removed["support"]["memberships"]["count"], 1)

    def test_tenant_analytics_snapshot_tracks_rollout_readiness(self) -> None:
        from backend.core.database import Base
        from backend.services import tenant_service, workspace_service

        engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
        Session = sessionmaker(bind=engine, expire_on_commit=False, future=True)
        Base.metadata.create_all(bind=engine)

        workspace_path = Path("tests") / "_tmp_analytics_workspaces.json"
        legacy_path = Path("tests") / "_tmp_analytics_legacy_workspaces.json"
        fake_settings = SimpleNamespace(
            demo_tenant_slug="alpha-desk",
            demo_tenant_name="Alpha Desk",
            demo_tenant_plan="pro",
            demo_user_id="demo-trader",
        )
        try:
            with (
                Session() as db,
                patch.object(tenant_service, "settings", fake_settings),
                patch.object(workspace_service, "_WORKSPACE_FILE", workspace_path),
                patch.object(workspace_service, "_LEGACY_WORKSPACE_FILE", legacy_path),
            ):
                identity = tenant_service.ensure_demo_tenant_for_user(
                    db,
                    auth_subject="demo-trader",
                    email="demo@example.test",
                    name="Demo Trader",
                    provider="local-demo",
                    requested_tenant_slug="alpha-desk",
                )
                current_user = SimpleNamespace(
                    tenant_id=identity["active_tenant"].id,
                    tenant_slug="alpha-desk",
                    user_id="demo-trader",
                    auth_subject="demo-trader",
                    role="owner",
                    platform_role="admin",
                )
                tenant_service.update_tenant_branding(
                    db,
                    current_user=current_user,
                    updates={
                        "app_name": "Pilot Console",
                        "support_email": "support@example.test",
                        "accent_primary": "#123456",
                    },
                )
                tenant_service.seed_tenant_onboarding_workspace(db, current_user=current_user)
                tenant_service.update_tenant_onboarding_step(
                    db,
                    current_user=current_user,
                    step_key="launch_review",
                    completed=True,
                )
                snapshot = tenant_service.get_tenant_analytics_snapshot(db, current_user=current_user, activity_limit=10)

            self.assertGreaterEqual(snapshot["summary"]["adoption_score"], 80)
            self.assertEqual(snapshot["summary"]["workspace_count"], 1)
            self.assertEqual(snapshot["summary"]["rollout_readiness"], 100)
            self.assertEqual(snapshot["flag_summary"]["enabled_count"], 6)
            self.assertGreaterEqual(snapshot["recent_activity"]["count"], 5)
            self.assertEqual(snapshot["summary"]["activation_stage"], "Pilot live")
        finally:
            if workspace_path.exists():
                workspace_path.unlink()
            if legacy_path.exists():
                legacy_path.unlink()

    def test_feature_flag_overrides_can_enable_and_reset_tenant_features(self) -> None:
        from backend.core.database import Base
        from backend.services import tenant_service

        engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
        Session = sessionmaker(bind=engine, expire_on_commit=False, future=True)
        Base.metadata.create_all(bind=engine)

        fake_settings = SimpleNamespace(
            demo_tenant_slug="alpha-desk",
            demo_tenant_name="Alpha Desk",
            demo_tenant_plan="starter",
        )
        with (
            Session() as db,
            patch.object(tenant_service, "settings", fake_settings),
        ):
            identity = tenant_service.ensure_demo_tenant_for_user(
                db,
                auth_subject="demo-trader",
                email="demo@example.test",
                name="Demo Trader",
                provider="local-demo",
                requested_tenant_slug="alpha-desk",
            )
            current_user = SimpleNamespace(
                tenant_id=identity["active_tenant"].id,
                tenant_slug="alpha-desk",
                user_id="demo-trader",
                auth_subject="demo-trader",
                role="owner",
                platform_role="admin",
            )
            before = tenant_service.get_tenant_feature_flags(db, current_user=current_user)
            updated = tenant_service.update_tenant_feature_flag(
                db,
                current_user=current_user,
                flag_key="tenant_branding",
                enabled=True,
            )
            reset = tenant_service.update_tenant_feature_flag(
                db,
                current_user=current_user,
                flag_key="tenant_branding",
                reset=True,
            )
            timeline = tenant_service.get_tenant_support_snapshot(db, current_user=current_user, limit=10)["timeline"]["items"]

        before_branding = next(item for item in before["items"] if item["key"] == "tenant_branding")
        updated_branding = next(item for item in updated["items"] if item["key"] == "tenant_branding")
        reset_branding = next(item for item in reset["items"] if item["key"] == "tenant_branding")
        self.assertFalse(before_branding["effective_enabled"])
        self.assertTrue(updated_branding["effective_enabled"])
        self.assertTrue(updated_branding["is_overridden"])
        self.assertEqual(updated_branding["source"], "override")
        self.assertFalse(reset_branding["effective_enabled"])
        self.assertFalse(reset_branding["is_overridden"])
        event_types = [item["event_type"] for item in timeline]
        self.assertIn("tenant.feature_flag_updated", event_types)

    def test_api_tokens_can_be_created_limited_and_revoked(self) -> None:
        from backend.core.database import Base
        from backend.services import tenant_service

        engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
        Session = sessionmaker(bind=engine, expire_on_commit=False, future=True)
        Base.metadata.create_all(bind=engine)

        fake_settings = SimpleNamespace(
            demo_tenant_slug="alpha-desk",
            demo_tenant_name="Alpha Desk",
            demo_tenant_plan="team",
            api_token_salt="unit-test-salt",
            partner_webhook_timeout_seconds=1,
            job_worker_batch_size=12,
        )
        with (
            Session() as db,
            patch.object(tenant_service, "settings", fake_settings),
        ):
            identity = tenant_service.ensure_demo_tenant_for_user(
                db,
                auth_subject="demo-trader",
                email="demo@example.test",
                name="Demo Trader",
                provider="local-demo",
                requested_tenant_slug="alpha-desk",
            )
            current_user = SimpleNamespace(
                tenant_id=identity["active_tenant"].id,
                tenant_slug="alpha-desk",
                user_id="demo-trader",
                auth_subject="demo-trader",
                role="owner",
                platform_role="admin",
            )
            tenant_service.update_tenant_feature_flag(
                db,
                current_user=current_user,
                flag_key="api_access",
                enabled=True,
                limit=1,
            )
            created = tenant_service.create_tenant_api_token(
                db,
                current_user=current_user,
                name="Partner ingest",
                scopes=["tenant.read", "market.read"],
                expires_in_days=30,
            )
            snapshot = tenant_service.get_tenant_api_tokens(db, current_user=current_user)
            auth_identity = tenant_service.authenticate_tenant_api_token(
                db,
                raw_token=created["token"]["secret"],
            )

            self.assertEqual(snapshot["tokens"]["active_count"], 1)
            self.assertEqual(created["token"]["name"], "Partner ingest")
            self.assertTrue(created["token"]["secret"].startswith("stk_live_"))
            self.assertEqual(auth_identity["tenant"].slug, "systematic-equities")
            self.assertEqual(auth_identity["token_name"], "Partner ingest")

            with self.assertRaises(ValidationError):
                tenant_service.create_tenant_api_token(
                    db,
                    current_user=current_user,
                    name="Second token",
                    scopes=["tenant.read"],
                    expires_in_days=30,
                )

            tenant_service.revoke_tenant_api_token(
                db,
                current_user=current_user,
                token_id=created["token"]["id"],
            )
            with self.assertRaises(UnauthorizedError):
                tenant_service.authenticate_tenant_api_token(
                    db,
                    raw_token=created["token"]["secret"],
                )

    def test_api_usage_snapshot_tracks_token_requests(self) -> None:
        from backend.core.database import Base
        from backend.services import tenant_service

        engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
        Session = sessionmaker(bind=engine, expire_on_commit=False, future=True)
        Base.metadata.create_all(bind=engine)

        fake_settings = SimpleNamespace(
            demo_tenant_slug="alpha-desk",
            demo_tenant_name="Alpha Desk",
            demo_tenant_plan="team",
            api_token_salt="unit-test-salt",
        )
        with (
            Session() as db,
            patch.object(tenant_service, "settings", fake_settings),
        ):
            identity = tenant_service.ensure_demo_tenant_for_user(
                db,
                auth_subject="demo-trader",
                email="demo@example.test",
                name="Demo Trader",
                provider="local-demo",
                requested_tenant_slug="alpha-desk",
            )
            current_user = SimpleNamespace(
                tenant_id=identity["active_tenant"].id,
                tenant_slug="alpha-desk",
                user_id="demo-trader",
                auth_subject="demo-trader",
                role="owner",
                platform_role="admin",
            )
            tenant_service.update_tenant_feature_flag(db, current_user=current_user, flag_key="api_access", enabled=True, limit=2)
            created = tenant_service.create_tenant_api_token(
                db,
                current_user=current_user,
                name="Usage probe",
                scopes=["tenant.read", "market.read"],
                expires_in_days=30,
            )
            tenant_service.record_tenant_api_request(
                db,
                tenant_id=current_user.tenant_id,
                token_id=created["token"]["id"],
                token_name=created["token"]["name"],
                scopes=["tenant.read", "market.read"],
                request_path="/api/market/dashboard",
                method="GET",
                status_code=200,
            )
            tenant_service.record_tenant_api_request(
                db,
                tenant_id=current_user.tenant_id,
                token_id=created["token"]["id"],
                token_name=created["token"]["name"],
                scopes=["tenant.read", "market.read"],
                request_path="/api/me",
                method="GET",
                status_code=200,
            )
            snapshot = tenant_service.get_tenant_api_usage_snapshot(db, current_user=current_user)

        self.assertEqual(snapshot["summary"]["total_requests"], 2)
        route_groups = {item["key"]: item["count"] for item in snapshot["route_groups"]}
        self.assertEqual(route_groups["market"], 1)
        self.assertEqual(route_groups["me"], 1)
        self.assertEqual(snapshot["tokens"][0]["count"], 2)

    def test_security_snapshot_rolls_up_token_webhook_and_auth_risks(self) -> None:
        from datetime import datetime, timedelta, timezone

        from backend.core.database import Base
        from backend.models.saas import Tenant
        from backend.services import rate_limit_service, tenant_service

        engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
        Session = sessionmaker(bind=engine, expire_on_commit=False, future=True)
        Base.metadata.create_all(bind=engine)

        fake_settings = SimpleNamespace(
            demo_tenant_slug="alpha-desk",
            demo_tenant_name="Alpha Desk",
            demo_tenant_plan="enterprise",
            api_token_salt="unit-test-salt",
            job_worker_batch_size=4,
            rate_limit_enabled=True,
            rate_limit_auth_failure_threshold=3,
            rate_limit_auth_window_seconds=600,
            rate_limit_auth_lockout_seconds=900,
        )
        with (
            Session() as db,
            patch.object(tenant_service, "settings", fake_settings),
            patch.object(rate_limit_service, "settings", fake_settings),
        ):
            rate_limit_service.reset_rate_limit_state()
            identity = tenant_service.ensure_demo_tenant_for_user(
                db,
                auth_subject="demo-trader",
                email="demo@example.test",
                name="Demo Trader",
                provider="local-demo",
                requested_tenant_slug="alpha-desk",
            )
            current_user = SimpleNamespace(
                tenant_id=identity["active_tenant"].id,
                tenant_slug="alpha-desk",
                user_id="demo-trader",
                auth_subject="demo-trader",
                role="owner",
                platform_role="admin",
            )
            tenant_service.update_tenant_feature_flag(db, current_user=current_user, flag_key="api_access", enabled=True, limit=5)
            tenant_service.update_tenant_feature_flag(db, current_user=current_user, flag_key="partner_webhooks", enabled=True, limit=3)

            created = tenant_service.create_tenant_api_token(
                db,
                current_user=current_user,
                name="Admin integration",
                scopes=["tenant.read", "tenant.admin"],
                expires_in_days=7,
            )
            tenant_service.record_tenant_api_request(
                db,
                tenant_id=current_user.tenant_id,
                token_id=created["token"]["id"],
                token_name=created["token"]["name"],
                scopes=["tenant.read", "tenant.admin"],
                request_path="/api/orgs/support",
                method="GET",
                status_code=200,
            )
            webhook_snapshot = tenant_service.create_tenant_partner_webhook(
                db,
                current_user=current_user,
                name="Launch hook",
                url="https://example.test/hooks/launch",
                events=["tenant.launch_ready"],
            )
            webhook_id = webhook_snapshot["webhooks"]["items"][0]["id"]
            tenant_service.run_tenant_partner_webhook_action(
                db,
                current_user=current_user,
                webhook_id=webhook_id,
                action="pause",
            )
            tenant_service.update_tenant_delivery_settings(
                db,
                current_user=current_user,
                updates={
                    "auth_policy": "require_sso",
                    "preferred_provider": "auth0",
                    "enabled_providers": ["auth0"],
                },
            )

            tenant = db.execute(select(Tenant).where(Tenant.id == current_user.tenant_id)).scalar_one()
            token_rows = tenant_service._read_api_token_state(tenant)
            token_rows[0]["created_at"] = (datetime.now(timezone.utc) - timedelta(days=45)).isoformat()
            token_rows[0]["last_used_at"] = None
            token_rows[0]["expires_at"] = (datetime.now(timezone.utc) + timedelta(days=5)).isoformat()
            tenant_service._write_api_token_state(tenant, token_rows)
            db.flush()
            db.commit()

            for _ in range(3):
                rate_limit_service.record_auth_failure(
                    action="login",
                    ip_address="10.0.0.1",
                    email="demo@example.test",
                    tenant_slug="alpha-desk",
                    reason="invalid_credentials",
                )
            for _ in range(13):
                try:
                    rate_limit_service.check_auth_flow_allowed(
                        action="login",
                        ip_address="10.0.0.2",
                        email="load@example.test",
                        tenant_slug="alpha-desk",
                    )
                except Exception:
                    break

            snapshot = tenant_service.get_tenant_security_snapshot(db, current_user=current_user, limit=8)

        self.assertEqual(snapshot["summary"]["status"], "critical")
        self.assertEqual(snapshot["tokens"]["admin_scope_count"], 1)
        self.assertEqual(snapshot["tokens"]["stale_active_count"], 1)
        self.assertEqual(snapshot["tokens"]["expiring_soon_count"], 1)
        self.assertEqual(snapshot["webhooks"]["paused_count"], 1)
        self.assertEqual(snapshot["auth"]["auth_policy"], "require_sso")
        self.assertFalse(snapshot["auth"]["launch_ready"])
        self.assertTrue(snapshot["auth"]["launch_blockers"])
        self.assertGreaterEqual(snapshot["rate_limits"]["blocked_actor_count"], 1)
        self.assertGreaterEqual(snapshot["rate_limits"]["throttle_event_count"], 1)
        self.assertGreaterEqual(snapshot["audit"]["count"], 3)
        event_types = {item["event_type"] for item in snapshot["audit"]["items"]}
        self.assertIn("tenant.api_token_created", event_types)
        self.assertIn("tenant.partner_webhook_pause", event_types)

    def test_rate_limit_snapshot_tracks_throttles_and_auth_lockouts(self) -> None:
        from backend.services.exceptions import TooManyRequestsError
        from backend.services import rate_limit_service

        fake_settings = SimpleNamespace(
            rate_limit_enabled=True,
            rate_limit_auth_failure_threshold=2,
            rate_limit_auth_window_seconds=600,
            rate_limit_auth_lockout_seconds=900,
        )
        with patch.object(rate_limit_service, "settings", fake_settings):
            rate_limit_service.reset_rate_limit_state()
            for _ in range(2):
                rate_limit_service.record_auth_failure(
                    action="login",
                    ip_address="192.168.1.10",
                    email="ops@example.test",
                    tenant_slug="alpha-desk",
                    reason="invalid_credentials",
                )
            with self.assertRaises(TooManyRequestsError):
                rate_limit_service.check_auth_flow_allowed(
                    action="login",
                    ip_address="192.168.1.10",
                    email="ops@example.test",
                    tenant_slug="alpha-desk",
                )
            snapshot = rate_limit_service.get_rate_limit_snapshot(tenant_slug="alpha-desk", limit=8)

        self.assertEqual(snapshot["summary"]["auth_lockout_count"], 2)
        self.assertGreaterEqual(snapshot["summary"]["blocked_actor_count"], 1)
        self.assertIsNotNone(snapshot["summary"]["last_abuse_event_at"])
        self.assertTrue(snapshot["blocked_actors"])

    def test_partner_webhooks_create_test_and_rotate(self) -> None:
        from backend.core.database import Base
        from backend.services import tenant_service

        class _Response:
            status = 200

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def read(self):
                return b"ok"

        engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
        Session = sessionmaker(bind=engine, expire_on_commit=False, future=True)
        Base.metadata.create_all(bind=engine)

        fake_settings = SimpleNamespace(
            demo_tenant_slug="alpha-desk",
            demo_tenant_name="Alpha Desk",
            demo_tenant_plan="team",
            api_token_salt="unit-test-salt",
        )
        with (
            Session() as db,
            patch.object(tenant_service, "settings", fake_settings),
            patch.object(tenant_service.urlrequest, "urlopen", return_value=_Response()),
        ):
            identity = tenant_service.ensure_demo_tenant_for_user(
                db,
                auth_subject="demo-trader",
                email="demo@example.test",
                name="Demo Trader",
                provider="local-demo",
                requested_tenant_slug="alpha-desk",
            )
            current_user = SimpleNamespace(
                tenant_id=identity["active_tenant"].id,
                tenant_slug="alpha-desk",
                user_id="demo-trader",
                auth_subject="demo-trader",
                role="owner",
                platform_role="admin",
            )
            tenant_service.update_tenant_feature_flag(db, current_user=current_user, flag_key="api_access", enabled=True, limit=2)
            created = tenant_service.create_tenant_partner_webhook(
                db,
                current_user=current_user,
                name="Ops bridge",
                url="https://example.test/hooks/ops",
                events=["tenant.launch_ready", "market.signal_ready"],
            )
            webhook_id = created["webhook"]["id"]
            tested = tenant_service.run_tenant_partner_webhook_action(
                db,
                current_user=current_user,
                webhook_id=webhook_id,
                action="send_test",
            )
            drained = tenant_service.get_tenant_partner_webhooks(db, current_user=current_user)
            rotated = tenant_service.run_tenant_partner_webhook_action(
                db,
                current_user=current_user,
                webhook_id=webhook_id,
                action="rotate_secret",
            )

        self.assertEqual(created["webhooks"]["count"], 1)
        self.assertEqual(tested["webhooks"]["jobs"]["summary"]["queued"], 1)
        self.assertEqual(drained["webhooks"]["deliveries"][0]["status"], "success")
        self.assertTrue(rotated["secret"]["secret"].startswith("whsec_"))

    def test_partner_webhook_jobs_retry_then_dead_letter(self) -> None:
        from datetime import datetime, timedelta, timezone

        from backend.core.database import Base
        from backend.models.saas import AsyncJob
        from backend.services import job_queue_service, tenant_service

        engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
        Session = sessionmaker(bind=engine, expire_on_commit=False, future=True)
        Base.metadata.create_all(bind=engine)

        fake_settings = SimpleNamespace(
            demo_tenant_slug="alpha-desk",
            demo_tenant_name="Alpha Desk",
            demo_tenant_plan="team",
            api_token_salt="unit-test-salt",
            partner_webhook_timeout_seconds=1,
            job_worker_batch_size=12,
        )

        with (
            Session() as db,
            patch.object(tenant_service, "settings", fake_settings),
            patch.object(tenant_service.urlrequest, "urlopen", side_effect=tenant_service.urlerror.URLError("connection refused")),
        ):
            identity = tenant_service.ensure_demo_tenant_for_user(
                db,
                auth_subject="demo-trader",
                email="demo@example.test",
                name="Demo Trader",
                provider="local-demo",
                requested_tenant_slug="alpha-desk",
            )
            current_user = SimpleNamespace(
                tenant_id=identity["active_tenant"].id,
                tenant_slug="alpha-desk",
                user_id="demo-trader",
                auth_subject="demo-trader",
                role="owner",
                platform_role="admin",
            )
            tenant_service.update_tenant_feature_flag(db, current_user=current_user, flag_key="api_access", enabled=True, limit=2)
            tenant_service.create_tenant_partner_webhook(
                db,
                current_user=current_user,
                name="Failing listener",
                url="https://example.test/hooks/fail",
                events=["market.signal_ready"],
            )
            tenant = tenant_service._resolve_tenant_for_current_user(db, current_user)
            dispatch = tenant_service._dispatch_partner_webhook_event(
                db,
                tenant=tenant,
                event_key="market.signal_ready",
                payload={"event": "market.signal_ready", "generated_at": "2026-04-16T10:00:00+00:00"},
            )
            db.commit()

            self.assertEqual(dispatch["queued"], 1)

            first_pass = tenant_service.get_tenant_partner_webhooks(db, current_user=current_user)
            self.assertEqual(first_pass["webhooks"]["jobs"]["summary"]["retrying"], 1)
            self.assertEqual(first_pass["webhooks"]["deliveries"][0]["status"], "failed")

            for _ in range(4):
                job = db.execute(select(AsyncJob).order_by(AsyncJob.created_at.desc())).scalar_one()
                job.available_at = datetime.now(timezone.utc) - timedelta(seconds=1)
                db.commit()
                job_queue_service.run_due_jobs(db, limit=1)

            final_snapshot = tenant_service.get_tenant_partner_webhooks(db, current_user=current_user)
            self.assertEqual(final_snapshot["webhooks"]["jobs"]["summary"]["dead_letter"], 1)
            self.assertTrue(final_snapshot["webhooks"]["jobs"]["dead_letters"])

    def test_enqueue_job_retries_sqlite_lock_once_before_succeeding(self) -> None:
        from backend.core.database import Base
        from backend.models.saas import AsyncJob
        from backend.services import job_queue_service

        engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
        Session = sessionmaker(bind=engine, expire_on_commit=False, future=True)
        Base.metadata.create_all(bind=engine)

        with Session() as db, patch.object(job_queue_service.time, "sleep", return_value=None):
            original_flush = db.flush
            state = {"calls": 0}

            def flaky_flush(*args, **kwargs):
                state["calls"] += 1
                if state["calls"] == 1:
                    raise OperationalError("INSERT INTO async_jobs ...", {}, Exception("database is locked"))
                return original_flush(*args, **kwargs)

            with patch.object(db, "flush", side_effect=flaky_flush):
                job = job_queue_service.enqueue_job(
                    db,
                    job_type="partner_webhook_delivery",
                    payload={"event": "market.signal_ready"},
                )
                db.commit()

            stored_jobs = list(db.execute(select(AsyncJob)).scalars())

        self.assertEqual(state["calls"], 2)
        self.assertEqual(len(stored_jobs), 1)
        self.assertEqual(stored_jobs[0].id, job.id)
        self.assertEqual(stored_jobs[0].status, "queued")

    def test_onboarding_templates_apply_workspace_and_audit(self) -> None:
        from backend.core.database import Base
        from backend.services import tenant_service, workspace_service

        engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
        Session = sessionmaker(bind=engine, expire_on_commit=False, future=True)
        Base.metadata.create_all(bind=engine)

        workspace_path = Path("tests") / "_tmp_template_workspaces.json"
        legacy_path = Path("tests") / "_tmp_template_legacy_workspaces.json"
        fake_settings = SimpleNamespace(
            demo_tenant_slug="alpha-desk",
            demo_tenant_name="Alpha Desk",
            demo_tenant_plan="team",
            demo_user_id="demo-trader",
        )
        try:
            with (
                Session() as db,
                patch.object(tenant_service, "settings", fake_settings),
                patch.object(workspace_service, "_WORKSPACE_FILE", workspace_path),
                patch.object(workspace_service, "_LEGACY_WORKSPACE_FILE", legacy_path),
            ):
                identity = tenant_service.ensure_demo_tenant_for_user(
                    db,
                    auth_subject="demo-trader",
                    email="demo@example.test",
                    name="Demo Trader",
                    provider="local-demo",
                    requested_tenant_slug="alpha-desk",
                )
                current_user = SimpleNamespace(
                    tenant_id=identity["active_tenant"].id,
                    tenant_slug="alpha-desk",
                    user_id="demo-trader",
                    auth_subject="demo-trader",
                    role="owner",
                    platform_role="admin",
                )
                applied = tenant_service.apply_tenant_onboarding_template(
                    db,
                    current_user=current_user,
                    template_key="ops_command_center",
                )
                snapshot = tenant_service.get_tenant_onboarding_templates_snapshot(db, current_user=current_user)
                support = tenant_service.get_tenant_support_snapshot(db, current_user=current_user, limit=20)
                workspaces = workspace_service.list_workspaces("demo-trader", tenant_slug="alpha-desk")

            self.assertEqual(applied["workspace"]["name"], "Ops Command Center")
            self.assertEqual(workspaces["count"], 1)
            self.assertEqual(snapshot["templates"]["applied_count"], 1)
            template_row = next(item for item in snapshot["templates"]["items"] if item["key"] == "ops_command_center")
            self.assertTrue(template_row["is_applied"])
            event_types = [item["event_type"] for item in support["timeline"]["items"]]
            self.assertIn("tenant.onboarding_template_applied", event_types)
        finally:
            if workspace_path.exists():
                workspace_path.unlink()
            if legacy_path.exists():
                legacy_path.unlink()

    def test_beta_template_requires_release_channels(self) -> None:
        from backend.core.database import Base
        from backend.services import tenant_service, workspace_service

        engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
        Session = sessionmaker(bind=engine, expire_on_commit=False, future=True)
        Base.metadata.create_all(bind=engine)

        workspace_path = Path("tests") / "_tmp_template_gate_workspaces.json"
        legacy_path = Path("tests") / "_tmp_template_gate_legacy_workspaces.json"
        fake_settings = SimpleNamespace(
            demo_tenant_slug="alpha-desk",
            demo_tenant_name="Alpha Desk",
            demo_tenant_plan="pro",
            demo_user_id="demo-trader",
        )
        try:
            with (
                Session() as db,
                patch.object(tenant_service, "settings", fake_settings),
                patch.object(workspace_service, "_WORKSPACE_FILE", workspace_path),
                patch.object(workspace_service, "_LEGACY_WORKSPACE_FILE", legacy_path),
            ):
                identity = tenant_service.ensure_demo_tenant_for_user(
                    db,
                    auth_subject="demo-trader",
                    email="demo@example.test",
                    name="Demo Trader",
                    provider="local-demo",
                    requested_tenant_slug="alpha-desk",
                )
                current_user = SimpleNamespace(
                    tenant_id=identity["active_tenant"].id,
                    tenant_slug="alpha-desk",
                    user_id="demo-trader",
                    auth_subject="demo-trader",
                    role="owner",
                    platform_role="admin",
                )
                tenant_service.update_tenant_feature_flag(
                    db,
                    current_user=current_user,
                    flag_key="onboarding_templates",
                    enabled=True,
                    limit=5,
                )
                with self.assertRaises(ForbiddenError):
                    tenant_service.apply_tenant_onboarding_template(
                        db,
                        current_user=current_user,
                        template_key="partner_beta_lab",
                    )
        finally:
            if workspace_path.exists():
                workspace_path.unlink()
            if legacy_path.exists():
                legacy_path.unlink()

    def test_delivery_settings_require_entitlement_then_persist(self) -> None:
        from backend.core.database import Base
        from backend.services import tenant_service

        engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
        Session = sessionmaker(bind=engine, expire_on_commit=False, future=True)
        Base.metadata.create_all(bind=engine)

        fake_settings = SimpleNamespace(
            demo_tenant_slug="alpha-desk",
            demo_tenant_name="Alpha Desk",
            demo_tenant_plan="starter",
        )
        with (
            Session() as db,
            patch.object(tenant_service, "settings", fake_settings),
        ):
            identity = tenant_service.ensure_demo_tenant_for_user(
                db,
                auth_subject="demo-trader",
                email="demo@example.test",
                name="Demo Trader",
                provider="local-demo",
                requested_tenant_slug="alpha-desk",
            )
            current_user = SimpleNamespace(
                tenant_id=identity["active_tenant"].id,
                tenant_slug="alpha-desk",
                user_id="demo-trader",
                auth_subject="demo-trader",
                role="owner",
                platform_role="admin",
            )
            with self.assertRaises(ForbiddenError):
                tenant_service.update_tenant_delivery_settings(
                    db,
                    current_user=current_user,
                    updates={"primary_domain": "desk.example.test"},
                )

            tenant_service.update_tenant_feature_flag(
                db,
                current_user=current_user,
                flag_key="custom_domains",
                enabled=True,
                limit=2,
            )
            tenant_service.update_tenant_feature_flag(
                db,
                current_user=current_user,
                flag_key="branded_email",
                enabled=True,
                limit=2,
            )
            snapshot = tenant_service.update_tenant_delivery_settings(
                db,
                current_user=current_user,
                updates={
                    "primary_domain": "desk.example.test",
                    "secondary_domains": ["pilot.example.test"],
                    "domain_status": "pending_verification",
                    "sender_name": "Alpha Desk",
                    "sender_email": "ops@desk.example.test",
                    "reply_to_email": "support@desk.example.test",
                    "mail_from_subdomain": "mail",
                    "email_signature": "Alpha Desk Support",
                },
            )

        custom_domains = snapshot["delivery"]["custom_domains"]
        branded_email = snapshot["delivery"]["branded_email"]
        self.assertTrue(custom_domains["configured"])
        self.assertEqual(custom_domains["primary_domain"], "desk.example.test")
        self.assertEqual(custom_domains["secondary_domains"], ["pilot.example.test"])
        self.assertEqual(custom_domains["domain_status"], "pending_verification")
        self.assertEqual(custom_domains["verification_host"], "_stocksignals.desk.example.test")
        self.assertTrue(branded_email["configured"])
        self.assertEqual(branded_email["sender_email"], "ops@desk.example.test")
        self.assertEqual(branded_email["mail_from_domain"], "mail.desk.example.test")

    def test_delivery_actions_progress_domain_and_sender_stack(self) -> None:
        from backend.core.database import Base
        from backend.services import tenant_service

        engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
        Session = sessionmaker(bind=engine, expire_on_commit=False, future=True)
        Base.metadata.create_all(bind=engine)

        fake_settings = SimpleNamespace(
            demo_tenant_slug="alpha-desk",
            demo_tenant_name="Alpha Desk",
            demo_tenant_plan="enterprise",
        )
        with (
            Session() as db,
            patch.object(tenant_service, "settings", fake_settings),
        ):
            identity = tenant_service.ensure_demo_tenant_for_user(
                db,
                auth_subject="demo-trader",
                email="demo@example.test",
                name="Demo Trader",
                provider="local-demo",
                requested_tenant_slug="alpha-desk",
            )
            current_user = SimpleNamespace(
                tenant_id=identity["active_tenant"].id,
                tenant_slug="alpha-desk",
                user_id="demo-trader",
                auth_subject="demo-trader",
                role="owner",
                platform_role="admin",
            )
            tenant_service.update_tenant_delivery_settings(
                db,
                current_user=current_user,
                updates={
                    "primary_domain": "desk.example.test",
                    "sender_name": "Alpha Desk",
                    "sender_email": "ops@desk.example.test",
                    "mail_from_subdomain": "mail",
                    "email_provider": "resend",
                    "release_channel": "pilot",
                },
            )
            tenant_service.run_tenant_delivery_action(db, current_user=current_user, action="request_verification")
            tenant_service.run_tenant_delivery_action(db, current_user=current_user, action="mark_verified")
            tenant_service.run_tenant_delivery_action(db, current_user=current_user, action="send_test")
            snapshot = tenant_service.run_tenant_delivery_action(db, current_user=current_user, action="activate_live")

        self.assertEqual(snapshot["delivery"]["custom_domains"]["domain_status"], "live")
        self.assertIsNotNone(snapshot["delivery"]["custom_domains"]["verified_at"])
        self.assertIsNotNone(snapshot["delivery"]["custom_domains"]["live_at"])
        self.assertEqual(snapshot["delivery"]["branded_email"]["provider_key"], "resend")
        self.assertEqual(snapshot["delivery"]["branded_email"]["provider_status"], "live")
        self.assertIsNotNone(snapshot["delivery"]["branded_email"]["last_test_at"])

    def test_delivery_release_channel_requires_release_channels_entitlement(self) -> None:
        from backend.core.database import Base
        from backend.services import tenant_service

        engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
        Session = sessionmaker(bind=engine, expire_on_commit=False, future=True)
        Base.metadata.create_all(bind=engine)

        fake_settings = SimpleNamespace(
            demo_tenant_slug="alpha-desk",
            demo_tenant_name="Alpha Desk",
            demo_tenant_plan="starter",
        )
        with (
            Session() as db,
            patch.object(tenant_service, "settings", fake_settings),
        ):
            identity = tenant_service.ensure_demo_tenant_for_user(
                db,
                auth_subject="demo-trader",
                email="demo@example.test",
                name="Demo Trader",
                provider="local-demo",
                requested_tenant_slug="alpha-desk",
            )
            current_user = SimpleNamespace(
                tenant_id=identity["active_tenant"].id,
                tenant_slug="alpha-desk",
                user_id="demo-trader",
                auth_subject="demo-trader",
                role="owner",
                platform_role="admin",
            )
            tenant_service.update_tenant_feature_flag(db, current_user=current_user, flag_key="custom_domains", enabled=True, limit=1)
            tenant_service.update_tenant_feature_flag(db, current_user=current_user, flag_key="branded_email", enabled=True, limit=1)

            with self.assertRaises(ForbiddenError):
                tenant_service.update_tenant_delivery_settings(
                    db,
                    current_user=current_user,
                    updates={
                        "primary_domain": "desk.example.test",
                        "email_provider": "resend",
                        "sender_email": "ops@desk.example.test",
                        "release_channel": "pilot",
                    },
                )

    def test_chart_entitlements_strip_advanced_indicators_for_basic_plan(self) -> None:
        from backend.services import market_service

        payload = {
            "ticker": "SPY",
            "interval": "5m",
            "overlays": {
                "ema_9": [1, 2, 3],
                "rsi_14": [50, 55, 60],
                "macd": [0.1, 0.2, 0.3],
                "idm_upper_band": [101, 102, 103],
            },
            "available_indicators": ["ema_9", "rsi_14", "macd", "idm_upper_band"],
            "strategy": {"available": True, "upper_band": 103},
        }

        filtered = market_service.apply_chart_entitlements(payload, advanced_indicators_enabled=False)

        self.assertEqual(set(filtered["overlays"].keys()), {"ema_9"})
        self.assertEqual(filtered["available_indicators"], ["ema_9"])
        self.assertFalse(filtered["strategy"]["available"])
        self.assertTrue(filtered["strategy"]["restricted"])
        self.assertFalse(filtered["capabilities"]["advanced_indicators"])

    def test_compare_entitlements_strip_advanced_and_execution_fields(self) -> None:
        from backend.services import market_service

        payload = {
            "rows": [
                {
                    "ticker": "SPY",
                    "setup_grade": "A setup",
                    "conviction_label": "HIGH CONVICTION",
                    "alignment_label": "Aligned",
                    "entry_low_price": 100,
                    "entry_high_price": 101,
                    "target_price": 105,
                    "stop_loss": 99,
                    "contract_symbol": "SPY260101C00100000",
                    "execution_action": "BUY NOW",
                    "trade_status": "ENTER NOW",
                }
            ],
            "charts": {
                "SPY": {
                    "overlays": {"ema_9": [1, 2], "rsi_14": [50, 60]},
                    "available_indicators": ["ema_9", "rsi_14"],
                    "strategy": {"available": True},
                }
            },
            "summary": {"leader": {"ticker": "SPY"}},
        }

        filtered = market_service.apply_compare_entitlements(
            payload,
            advanced_indicators_enabled=False,
            broker_execution_enabled=False,
        )

        row = filtered["rows"][0]
        self.assertIsNone(row["setup_grade"])
        self.assertIsNone(row["conviction_label"])
        self.assertIsNone(row["entry_low_price"])
        self.assertIsNone(row["contract_symbol"])
        self.assertIsNone(row["execution_action"])
        self.assertFalse(filtered["charts"]["SPY"]["capabilities"]["advanced_indicators"])
        self.assertEqual(filtered["charts"]["SPY"]["available_indicators"], ["ema_9"])
        self.assertEqual(len(filtered["messages"]), 2)

    def test_billing_summary_exposes_plan_driven_entitlements(self) -> None:
        from backend.core.database import Base
        from backend.services import billing_service, tenant_service

        engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
        Session = sessionmaker(bind=engine, expire_on_commit=False, future=True)
        Base.metadata.create_all(bind=engine)

        fake_settings = SimpleNamespace(
            demo_tenant_slug="alpha-desk",
            demo_tenant_name="Alpha Desk",
            demo_tenant_plan="pro",
        )
        with (
            Session() as db,
            patch.object(tenant_service, "settings", fake_settings),
        ):
            identity = tenant_service.ensure_demo_tenant_for_user(
                db,
                auth_subject="demo-trader",
                email="demo@example.test",
                name="Demo Trader",
                provider="local-demo",
                requested_tenant_slug="alpha-desk",
            )
            summary = billing_service.get_billing_summary(
                db,
                SimpleNamespace(tenant_id=identity["active_tenant"].id),
            )

        entitlement_map = {item["key"]: item for item in summary["entitlements"]["items"]}
        self.assertEqual(summary["plan"]["key"], "pro")
        self.assertTrue(entitlement_map["realtime_streaming"]["enabled"])
        self.assertEqual(entitlement_map["organization_members"]["limit"], "5")
        self.assertEqual(summary["sync"]["status"], "demo")
        self.assertEqual(summary["events"]["count"], 0)

    def test_change_tenant_plan_syncs_plan_entitlements(self) -> None:
        from backend.core.database import Base
        from backend.services import billing_service, tenant_service

        engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
        Session = sessionmaker(bind=engine, expire_on_commit=False, future=True)
        Base.metadata.create_all(bind=engine)

        fake_settings = SimpleNamespace(
            demo_tenant_slug="alpha-desk",
            demo_tenant_name="Alpha Desk",
            demo_tenant_plan="pro",
        )
        with (
            Session() as db,
            patch.object(tenant_service, "settings", fake_settings),
        ):
            identity = tenant_service.ensure_demo_tenant_for_user(
                db,
                auth_subject="demo-trader",
                email="demo@example.test",
                name="Demo Trader",
                provider="local-demo",
                requested_tenant_slug="alpha-desk",
            )
            summary = billing_service.change_tenant_plan(
                db,
                SimpleNamespace(tenant_id=identity["active_tenant"].id),
                "team",
            )

        entitlement_map = {item["key"]: item for item in summary["entitlements"]["items"]}
        self.assertEqual(summary["plan"]["key"], "team")
        self.assertTrue(entitlement_map["broker_execution"]["enabled"])
        self.assertEqual(entitlement_map["organization_members"]["limit"], "20")

    def test_billing_summary_usage_reflects_tenant_workspaces(self) -> None:
        from backend.core.database import Base
        from backend.services import billing_service, tenant_service, workspace_service

        engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
        Session = sessionmaker(bind=engine, expire_on_commit=False, future=True)
        Base.metadata.create_all(bind=engine)

        workspace_path = Path("tests") / "_tmp_usage_workspaces.json"
        legacy_path = Path("tests") / "_tmp_usage_legacy_workspaces.json"
        fake_settings = SimpleNamespace(
            demo_tenant_slug="alpha-desk",
            demo_tenant_name="Alpha Desk",
            demo_tenant_plan="pro",
        )
        try:
            with (
                Session() as db,
                patch.object(tenant_service, "settings", fake_settings),
                patch.object(workspace_service, "_WORKSPACE_FILE", workspace_path),
                patch.object(workspace_service, "_LEGACY_WORKSPACE_FILE", legacy_path),
            ):
                identity = tenant_service.ensure_demo_tenant_for_user(
                    db,
                    auth_subject="demo-trader",
                    email="demo@example.test",
                    name="Demo Trader",
                    provider="local-demo",
                    requested_tenant_slug="alpha-desk",
                )
                workspace_service.save_workspace("demo-trader", "Desk 1", "dashboard", {"ticker": "SPY"}, tenant_slug="alpha-desk")
                workspace_service.save_workspace("demo-trader", "Desk 2", "dashboard", {"ticker": "QQQ"}, tenant_slug="alpha-desk")
                workspace_service.save_workspace("demo-trader", "Desk 3", "dashboard", {"ticker": "IWM"}, tenant_slug="pilot-fund")

                summary = billing_service.get_billing_summary(
                    db,
                    SimpleNamespace(
                        tenant_id=identity["active_tenant"].id,
                        tenant_slug="alpha-desk",
                        user_id="demo-trader",
                    ),
                )

            self.assertEqual(summary["usage"]["workspaces"]["used"], 2)
            self.assertEqual(summary["usage"]["layouts"]["used"], 2)
        finally:
            if workspace_path.exists():
                workspace_path.unlink()
            if legacy_path.exists():
                legacy_path.unlink()

    def test_demo_checkout_session_applies_plan_and_returns_summary(self) -> None:
        from backend.core.database import Base
        from backend.services import billing_service, tenant_service

        engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
        Session = sessionmaker(bind=engine, expire_on_commit=False, future=True)
        Base.metadata.create_all(bind=engine)

        fake_settings = SimpleNamespace(
            demo_tenant_slug="alpha-desk",
            demo_tenant_name="Alpha Desk",
            demo_tenant_plan="pro",
        )
        with (
            Session() as db,
            patch.object(tenant_service, "settings", fake_settings),
        ):
            identity = tenant_service.ensure_demo_tenant_for_user(
                db,
                auth_subject="demo-trader",
                email="demo@example.test",
                name="Demo Trader",
                provider="local-demo",
                requested_tenant_slug="alpha-desk",
            )
            payload = billing_service.create_billing_checkout_session(
                db,
                SimpleNamespace(
                    tenant_id=identity["active_tenant"].id,
                    tenant_slug="alpha-desk",
                    user_id="demo-trader",
                    email="demo@example.test",
                ),
                plan_key="team",
                billing_cycle="annual",
            )

        self.assertEqual(payload["mode"], "demo")
        self.assertEqual(payload["summary"]["plan"]["key"], "team")
        self.assertEqual(payload["summary"]["subscription"]["plan_key"], "team")
        self.assertEqual(payload["billing_cycle"], "annual")
        self.assertEqual(payload["summary"]["events"]["count"], 1)
        self.assertEqual(payload["summary"]["sync"]["status"], "demo")

    def test_billing_webhook_event_updates_subscription_state(self) -> None:
        from backend.core.database import Base
        from backend.services import billing_service, tenant_service

        engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
        Session = sessionmaker(bind=engine, expire_on_commit=False, future=True)
        Base.metadata.create_all(bind=engine)

        fake_settings = SimpleNamespace(
            demo_tenant_slug="alpha-desk",
            demo_tenant_name="Alpha Desk",
            demo_tenant_plan="pro",
        )
        with (
            Session() as db,
            patch.object(tenant_service, "settings", fake_settings),
        ):
            identity = tenant_service.ensure_demo_tenant_for_user(
                db,
                auth_subject="demo-trader",
                email="demo@example.test",
                name="Demo Trader",
                provider="local-demo",
                requested_tenant_slug="alpha-desk",
            )
            result = billing_service.process_billing_webhook_event(
                db,
                {
                    "type": "checkout.session.completed",
                    "data": {
                        "object": {
                            "id": "cs_test_123",
                            "customer": "cus_test_123",
                            "subscription": "sub_test_123",
                            "metadata": {"tenant_slug": "alpha-desk", "plan_key": "team"},
                        }
                    },
                },
            )
            summary = billing_service.get_billing_summary(
                db,
                SimpleNamespace(
                    tenant_id=identity["active_tenant"].id,
                    tenant_slug="alpha-desk",
                    user_id="demo-trader",
                ),
            )

        self.assertTrue(result["handled"])
        self.assertEqual(summary["plan"]["key"], "team")
        self.assertEqual(summary["subscription"]["provider"], "stripe")
        self.assertEqual(summary["subscription"]["external_customer_id"], "cus_test_123")
        self.assertEqual(summary["subscription"]["external_subscription_id"], "sub_test_123")
        self.assertEqual(summary["sync"]["status"], "healthy")
        self.assertEqual(summary["events"]["count"], 1)
        self.assertEqual(summary["events"]["items"][0]["status"], "processed")

    def test_billing_webhook_duplicate_event_is_idempotent_and_recorded(self) -> None:
        from backend.core.database import Base
        from backend.services import billing_service, tenant_service

        engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
        Session = sessionmaker(bind=engine, expire_on_commit=False, future=True)
        Base.metadata.create_all(bind=engine)

        fake_settings = SimpleNamespace(
            demo_tenant_slug="alpha-desk",
            demo_tenant_name="Alpha Desk",
            demo_tenant_plan="pro",
        )
        with (
            Session() as db,
            patch.object(tenant_service, "settings", fake_settings),
        ):
            identity = tenant_service.ensure_demo_tenant_for_user(
                db,
                auth_subject="demo-trader",
                email="demo@example.test",
                name="Demo Trader",
                provider="local-demo",
                requested_tenant_slug="alpha-desk",
            )
            event = {
                "id": "evt_stripe_123",
                "type": "checkout.session.completed",
                "data": {
                    "object": {
                        "id": "cs_test_123",
                        "customer": "cus_test_123",
                        "subscription": "sub_test_123",
                        "metadata": {"tenant_slug": "alpha-desk", "plan_key": "team"},
                    }
                },
            }
            first_result = billing_service.process_billing_webhook_event(db, event)
            second_result = billing_service.process_billing_webhook_event(db, event)
            summary = billing_service.get_billing_summary(
                db,
                SimpleNamespace(
                    tenant_id=identity["active_tenant"].id,
                    tenant_slug="alpha-desk",
                    user_id="demo-trader",
                ),
            )

        self.assertTrue(first_result["handled"])
        self.assertTrue(second_result["handled"])
        self.assertTrue(second_result["duplicate"])
        self.assertEqual(summary["plan"]["key"], "team")
        self.assertEqual(summary["events"]["count"], 2)
        self.assertEqual(summary["events"]["status_counts"]["processed"], 1)
        self.assertEqual(summary["events"]["status_counts"]["duplicate"], 1)

    def test_billing_sync_marks_stale_stripe_state_and_exposes_recovery_actions(self) -> None:
        from backend.core.database import Base
        from backend.services import billing_service, tenant_service

        engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
        Session = sessionmaker(bind=engine, expire_on_commit=False, future=True)
        Base.metadata.create_all(bind=engine)

        fake_settings = SimpleNamespace(
            demo_tenant_slug="alpha-desk",
            demo_tenant_name="Alpha Desk",
            demo_tenant_plan="pro",
        )
        fake_billing_settings = SimpleNamespace(
            stripe_secret_key="",
            billing_checkout_success_url="http://localhost:5173/settings?billing=success",
            billing_checkout_cancel_url="http://localhost:5173/settings?billing=cancel",
            billing_sync_stale_hours=24,
        )
        with (
            Session() as db,
            patch.object(tenant_service, "settings", fake_settings),
        ):
            identity = tenant_service.ensure_demo_tenant_for_user(
                db,
                auth_subject="demo-trader",
                email="demo@example.test",
                name="Demo Trader",
                provider="local-demo",
                requested_tenant_slug="alpha-desk",
            )
            with patch.object(
                billing_service,
                "_utc_now",
                return_value=billing_service._coerce_datetime("2026-04-19T10:00:00+00:00"),
            ):
                billing_service.process_billing_webhook_event(
                    db,
                    {
                        "type": "checkout.session.completed",
                        "data": {
                            "object": {
                                "id": "cs_stale_123",
                                "customer": "cus_stale_123",
                                "subscription": "sub_stale_123",
                                "metadata": {"tenant_slug": "alpha-desk", "plan_key": "team"},
                            }
                        }
                    },
                )
            with (
                patch.object(billing_service, "settings", fake_billing_settings),
                patch.object(
                    billing_service,
                    "_utc_now",
                    return_value=billing_service._coerce_datetime("2026-04-20T15:00:00+00:00"),
                ),
            ):
                summary = billing_service.get_billing_summary(
                    db,
                    SimpleNamespace(
                        tenant_id=identity["active_tenant"].id,
                        tenant_slug="alpha-desk",
                        user_id="demo-trader",
                    ),
                )

        self.assertEqual(summary["sync"]["status"], "stale")
        self.assertTrue(summary["sync"]["needs_reconciliation"])
        self.assertIn("reconcile", summary["sync"]["available_actions"])
        self.assertEqual(summary["recovery"]["failed_event_count"], 0)

    def test_billing_recovery_job_replays_failed_event_and_updates_summary(self) -> None:
        from backend.core.database import Base
        from backend.services import billing_service, job_queue_service, tenant_service

        engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
        Session = sessionmaker(bind=engine, expire_on_commit=False, future=True)
        Base.metadata.create_all(bind=engine)

        fake_settings = SimpleNamespace(
            demo_tenant_slug="alpha-desk",
            demo_tenant_name="Alpha Desk",
            demo_tenant_plan="pro",
        )
        with (
            Session() as db,
            patch.object(tenant_service, "settings", fake_settings),
        ):
            identity = tenant_service.ensure_demo_tenant_for_user(
                db,
                auth_subject="demo-trader",
                email="demo@example.test",
                name="Demo Trader",
                provider="local-demo",
                requested_tenant_slug="alpha-desk",
            )
            tenant = identity["active_tenant"]
            subscription = billing_service.ensure_subscription_record(db, tenant)
            billing_service._record_billing_event(
                db,
                tenant=tenant,
                provider="stripe",
                source="webhook",
                event_key="checkout.session.completed",
                external_event_id="evt_recover_123",
                status="failed",
                plan_key="team",
                external_customer_id="cus_recover_123",
                external_subscription_id="sub_recover_123",
                payload={
                    "event_id": "evt_recover_123",
                    "type": "checkout.session.completed",
                    "object": {
                        "id": "cs_recover_123",
                        "customer": "cus_recover_123",
                        "subscription": "sub_recover_123",
                        "metadata": {"tenant_slug": "alpha-desk", "plan_key": "team"},
                    },
                },
                result={"handled": False},
                error_message="timed out while processing",
            )
            db.commit()

            current_user = SimpleNamespace(
                tenant_id=tenant.id,
                tenant_slug="alpha-desk",
                user_id="demo-trader",
            )
            queued = billing_service.queue_billing_recovery_action(db, current_user, action="retry_last_failure")
            job_queue_service.run_due_jobs(db)
            db.expire_all()
            db.refresh(subscription)
            summary = billing_service.get_billing_summary(db, current_user)

        self.assertTrue(queued["queued"])
        self.assertEqual(summary["plan"]["key"], "team")
        self.assertEqual(summary["subscription"]["external_customer_id"], "cus_recover_123")
        self.assertEqual(summary["recovery"]["last_recovery_action"], "retry_last_failure")
        self.assertEqual(summary["recovery"]["last_recovery_status"], "succeeded")
        self.assertEqual(summary["recovery"]["jobs"]["summary"]["succeeded"], 1)
        self.assertTrue(any(item["event_key"] == "billing.recovery_replayed" for item in summary["events"]["items"]))

    def test_billing_ops_snapshot_tracks_drill_history_and_replays(self) -> None:
        from backend.core.database import Base
        from backend.services import billing_service, job_queue_service, tenant_service

        engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
        Session = sessionmaker(bind=engine, expire_on_commit=False, future=True)
        Base.metadata.create_all(bind=engine)

        fake_settings = SimpleNamespace(
            demo_tenant_slug="alpha-desk",
            demo_tenant_name="Alpha Desk",
            demo_tenant_plan="pro",
        )
        with (
            Session() as db,
            patch.object(tenant_service, "settings", fake_settings),
        ):
            identity = tenant_service.ensure_demo_tenant_for_user(
                db,
                auth_subject="demo-trader",
                email="demo@example.test",
                name="Demo Trader",
                provider="local-demo",
                requested_tenant_slug="alpha-desk",
            )
            tenant = identity["active_tenant"]
            subscription = billing_service.ensure_subscription_record(db, tenant)
            failed_row = billing_service._record_billing_event(
                db,
                tenant=tenant,
                provider="stripe",
                source="webhook",
                event_key="checkout.session.completed",
                external_event_id="evt_recover_ops_123",
                status="failed",
                plan_key="team",
                external_customer_id="cus_recover_ops_123",
                external_subscription_id="sub_recover_ops_123",
                payload={
                    "event_id": "evt_recover_ops_123",
                    "type": "checkout.session.completed",
                    "object": {
                        "id": "cs_recover_ops_123",
                        "customer": "cus_recover_ops_123",
                        "subscription": "sub_recover_ops_123",
                        "metadata": {"tenant_slug": "alpha-desk", "plan_key": "team"},
                    },
                },
                result={"handled": False},
                error_message="provider timeout",
            )
            db.commit()

            current_user = SimpleNamespace(
                tenant_id=tenant.id,
                tenant_slug="alpha-desk",
                user_id="demo-trader",
            )
            billing_service.queue_billing_recovery_action(db, current_user, action="retry_last_failure")
            job_queue_service.run_due_jobs(db)
            db.expire_all()
            db.refresh(subscription)

            event_snapshot = billing_service._build_billing_event_snapshot(db, tenant)
            recovery_snapshot = billing_service._build_billing_recovery_snapshot(db, tenant, subscription, event_snapshot)
            sync_snapshot = billing_service._build_billing_sync_snapshot(subscription, event_snapshot, recovery_snapshot)
            ops_snapshot = billing_service._build_billing_ops_snapshot(
                db,
                tenant,
                subscription,
                event_snapshot,
                recovery_snapshot,
                sync_snapshot,
            )

        self.assertEqual(ops_snapshot["summary"]["replay_count"], 1)
        self.assertEqual(ops_snapshot["summary"]["pending_job_count"], 0)
        self.assertEqual(ops_snapshot["recovery"]["last_recovery_action"], "retry_last_failure")
        self.assertEqual(ops_snapshot["drills"]["items"][0]["kind"], "replay")
        self.assertEqual(ops_snapshot["drills"]["items"][0]["failed_event_id"], failed_row.id)

    def test_order_lifecycle_health_snapshot_flags_stale_pending_and_rejects(self) -> None:
        from backend.services import trade_service

        pending_orders = pd.DataFrame(
            [
                {
                    "order_id": "ord_stale_1",
                    "trade_id": "trade_stale_1",
                    "ticker": "SPY",
                    "order_type": "limit",
                    "time_in_force": "day",
                    "updated_at": "2026-04-15T09:00:00+00:00",
                }
            ]
        )
        order_events = {
            "items": [
                {
                    "id": "evt_reject_1",
                    "trade_id": "trade_stale_1",
                    "ticker": "SPY",
                    "status": "rejected",
                    "order_type": "limit",
                    "detail": "Rejected by route guard.",
                    "created_at": "2026-04-16T10:00:00+00:00",
                },
                {
                    "id": "evt_fill_1",
                    "trade_id": "trade_fill_1",
                    "ticker": "QQQ",
                    "status": "filled",
                    "order_type": "market",
                    "label": "Filled",
                    "created_at": "2026-04-16T10:05:00+00:00",
                },
            ],
            "count": 2,
            "status_counts": {"rejected": 1, "filled": 1},
        }

        with (
            patch.object(trade_service, "get_pending_orders_snapshot", return_value={"items": pending_orders.to_dict(orient="records"), "count": 1}),
            patch.object(trade_service, "get_order_events_snapshot", return_value=order_events),
            patch("backend.services.trade_service.pd.Timestamp.now", return_value=pd.Timestamp("2026-04-16T12:00:00+00:00")),
        ):
            snapshot = trade_service.get_order_lifecycle_health_snapshot()

        self.assertEqual(snapshot["summary"]["status"], "warning")
        self.assertEqual(snapshot["summary"]["stale_pending_count"], 1)
        self.assertEqual(snapshot["summary"]["reject_count"], 1)
        self.assertEqual(snapshot["summary"]["fill_count"], 1)
        self.assertEqual(snapshot["stale_pending_orders"][0]["order_id"], "ord_stale_1")
        self.assertEqual(snapshot["recent_rejections"][0]["id"], "evt_reject_1")

    def test_workspace_limit_and_realtime_entitlement_follow_plan(self) -> None:
        from backend.core.database import Base
        from backend.services import billing_service, tenant_service, workspace_service

        engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
        Session = sessionmaker(bind=engine, expire_on_commit=False, future=True)
        Base.metadata.create_all(bind=engine)

        workspace_path = Path("tests") / "_tmp_limit_workspaces.json"
        legacy_path = Path("tests") / "_tmp_limit_legacy_workspaces.json"
        fake_settings = SimpleNamespace(
            demo_tenant_slug="alpha-desk",
            demo_tenant_name="Alpha Desk",
            demo_tenant_plan="starter",
        )
        try:
            with (
                Session() as db,
                patch.object(tenant_service, "settings", fake_settings),
                patch.object(workspace_service, "_WORKSPACE_FILE", workspace_path),
                patch.object(workspace_service, "_LEGACY_WORKSPACE_FILE", legacy_path),
            ):
                identity = tenant_service.ensure_demo_tenant_for_user(
                    db,
                    auth_subject="demo-trader",
                    email="demo@example.test",
                    name="Demo Trader",
                    provider="local-demo",
                    requested_tenant_slug="alpha-desk",
                )
                current_user = SimpleNamespace(
                    tenant_id=identity["active_tenant"].id,
                    tenant_slug="alpha-desk",
                    user_id="demo-trader",
                )
                workspace_service.save_workspace("demo-trader", "Desk 1", "dashboard", {"ticker": "SPY"}, tenant_slug="alpha-desk")
                workspace_service.save_workspace("demo-trader", "Desk 2", "dashboard", {"ticker": "QQQ"}, tenant_slug="alpha-desk")
                workspace_service.save_workspace("demo-trader", "Desk 3", "dashboard", {"ticker": "IWM"}, tenant_slug="alpha-desk")

                self.assertFalse(billing_service.has_entitlement(db, current_user, "realtime_streaming"))
                with self.assertRaises(ValidationError):
                    billing_service.enforce_entitlement_limit(
                        db,
                        current_user,
                        "workspace_count",
                        requested_total=4,
                        resource_label="workspaces",
                    )
        finally:
            if workspace_path.exists():
                workspace_path.unlink()
            if legacy_path.exists():
                legacy_path.unlink()

    def test_activate_tenant_for_user_switches_default_membership(self) -> None:
        from backend.core.database import Base
        from backend.services import tenant_service

        engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
        Session = sessionmaker(bind=engine, expire_on_commit=False, future=True)
        Base.metadata.create_all(bind=engine)

        fake_settings = SimpleNamespace(
            demo_tenant_slug="alpha-desk",
            demo_tenant_name="Alpha Desk",
            demo_tenant_plan="pro",
        )
        with (
            Session() as db,
            patch.object(tenant_service, "settings", fake_settings),
        ):
            identity = tenant_service.ensure_demo_tenant_for_user(
                db,
                auth_subject="demo-trader",
                email="demo@example.test",
                name="Demo Trader",
                provider="local-demo",
                requested_tenant_slug="alpha-desk",
            )
            user = identity["user"]
            tenant_service.create_tenant(db, owner=user, name="Pilot Fund", plan_key="starter")
            switched = tenant_service.activate_tenant_for_user(db, user=user, tenant_slug="pilot-fund")

        default_membership = next(item for item in switched["memberships"] if item["is_default"])
        self.assertEqual(default_membership["tenant"]["slug"], "pilot-fund")

    def test_chart_payload_uses_short_lived_cache_and_records_operation_hits(self) -> None:
        from backend.services import market_service, ops_service

        frame = pd.DataFrame(
            {
                "Open": [100.0, 101.0, 102.0],
                "High": [101.0, 102.0, 103.0],
                "Low": [99.5, 100.5, 101.5],
                "Close": [100.5, 101.5, 102.5],
                "Volume": [1000.0, 1200.0, 1400.0],
            },
            index=pd.to_datetime(
                [
                    "2026-01-02T09:30:00Z",
                    "2026-01-02T09:35:00Z",
                    "2026-01-02T09:40:00Z",
                ]
            ),
        )

        market_service.clear_market_response_cache()
        ops_service.reset_operation_metrics()
        with (
            patch.object(
                market_service,
                "make_settings",
                return_value=SimpleNamespace(period="5d", interval="5m"),
            ),
            patch.object(market_service.sdm, "download_ohlcv", return_value=frame) as download_mock,
            patch.object(
                market_service,
                "build_intraday_momentum_snapshot",
                return_value={"available": True, "overlays": {}},
            ),
        ):
            first = market_service.get_chart_payload(ChartRequest(ticker="SPY", interval="5m", points_limit=50))
            second = market_service.get_chart_payload(ChartRequest(ticker="SPY", interval="5m", points_limit=50))

        snapshot = ops_service.get_operation_metrics_snapshot()

        self.assertEqual(download_mock.call_count, 1)
        self.assertEqual(first["point_count"], second["point_count"])
        self.assertEqual(snapshot["summary"]["cache_hit_count"], 1)
        self.assertEqual(snapshot["summary"]["cache_miss_count"], 1)
        self.assertEqual(snapshot["operations"][0]["key"], "market.chart_payload")

    def test_market_data_freshness_snapshot_flags_stale_during_active_session(self) -> None:
        from backend.services import market_service

        snapshot = market_service._build_market_data_freshness_snapshot(
            ticker="SPY",
            interval="5m",
            latest_bar_at="2026-04-17T13:35:00+00:00",
            point_count=120,
            source="probe",
            checked_at=datetime(2026, 4, 17, 14, 25, tzinfo=timezone.utc),
        )

        self.assertEqual(snapshot["status"], "stale")
        self.assertTrue(snapshot["feed_expected"])
        self.assertGreater(snapshot["latest_bar_age_seconds"], snapshot["stale_threshold_seconds"])

    def test_market_data_freshness_snapshot_awaits_regular_session_premarket_when_regular_hours_only(self) -> None:
        from backend.services import market_service

        snapshot = market_service._build_market_data_freshness_snapshot(
            ticker="SPY",
            interval="5m",
            latest_bar_at="2026-04-16T20:00:00+00:00",
            point_count=120,
            regular_hours_only=True,
            source="probe",
            checked_at=datetime(2026, 4, 17, 12, 0, tzinfo=timezone.utc),
        )

        self.assertEqual(snapshot["status"], "awaiting_regular_session")
        self.assertFalse(snapshot["feed_expected"])
        self.assertEqual(snapshot["session"], "premarket")
        self.assertEqual(snapshot["session_policy"], "regular_hours_only")

    def test_market_data_freshness_snapshot_tracks_premarket_when_session_flex(self) -> None:
        from backend.services import market_service

        snapshot = market_service._build_market_data_freshness_snapshot(
            ticker="SPY",
            interval="5m",
            latest_bar_at="2026-04-16T20:00:00+00:00",
            point_count=120,
            regular_hours_only=False,
            source="probe",
            checked_at=datetime(2026, 4, 17, 12, 0, tzinfo=timezone.utc),
        )

        self.assertNotEqual(snapshot["status"], "awaiting_regular_session")
        self.assertTrue(snapshot["feed_expected"])
        self.assertEqual(snapshot["session"], "premarket")
        self.assertEqual(snapshot["session_mode"], "pre_market")
        self.assertEqual(snapshot["session_policy"], "session_flex")
        self.assertTrue(snapshot["session_profile"]["extended_hours"])

    def test_market_data_freshness_snapshot_flags_stale_after_hours_when_session_flex(self) -> None:
        from backend.services import market_service

        snapshot = market_service._build_market_data_freshness_snapshot(
            ticker="SPY",
            interval="5m",
            latest_bar_at="2026-04-17T20:00:00+00:00",
            point_count=120,
            regular_hours_only=False,
            source="probe",
            checked_at=datetime(2026, 4, 17, 22, 30, tzinfo=timezone.utc),
        )

        self.assertEqual(snapshot["status"], "stale")
        self.assertTrue(snapshot["feed_expected"])
        self.assertEqual(snapshot["session"], "after_hours")
        self.assertEqual(snapshot["session_policy"], "session_flex")

    def test_market_data_freshness_snapshot_awaits_regular_session_on_weekend_when_regular_hours_only(self) -> None:
        from backend.services import market_service

        snapshot = market_service._build_market_data_freshness_snapshot(
            ticker="SPY",
            interval="5m",
            latest_bar_at="2026-04-17T20:00:00+00:00",
            point_count=120,
            regular_hours_only=True,
            source="probe",
            checked_at=datetime(2026, 4, 18, 15, 0, tzinfo=timezone.utc),
        )

        self.assertEqual(snapshot["status"], "awaiting_regular_session")
        self.assertFalse(snapshot["feed_expected"])
        self.assertEqual(snapshot["session"], "weekend")
        self.assertEqual(snapshot["session_policy"], "regular_hours_only")

    def test_execution_context_waits_for_regular_session_when_freshness_policy_blocks_off_session(self) -> None:
        from backend.services import market_service

        context = market_service._build_execution_context(
            report={},
            freshness={
                "status": "awaiting_regular_session",
                "session": "premarket",
                "session_label": "Premarket",
            },
        )

        self.assertEqual(context["fill_quality"], "waiting")
        self.assertEqual(context["fill_label"], "Await regular session")
        self.assertEqual(context["size_cap_ratio"], 0.0)
        self.assertIn("waiting for core-session liquidity", context["route_label"])

    def test_execution_context_uses_extended_hours_limit_profile_for_session_flex_equities(self) -> None:
        from backend.services import market_service

        context = market_service._build_execution_context(
            report={},
            freshness={
                "status": "fresh",
                "session": "premarket",
                "session_mode": "pre_market",
                "session_label": "Premarket",
                "session_policy": "session_flex",
            },
        )

        self.assertEqual(context["instrument_type"], "equity")
        self.assertEqual(context["fill_quality"], "price_control")
        self.assertEqual(context["fill_label"], "Extended-hours price control")
        self.assertEqual(context["preferred_order_type"], "limit")
        self.assertEqual(context["session_mode"], "pre_market")
        self.assertGreater(context["size_cap_ratio"], 0.0)
        self.assertIn("DAY_EXT", context["route_label"])

    def test_chart_payload_includes_market_data_freshness(self) -> None:
        from backend.services import market_service

        frame = pd.DataFrame(
            {
                "Open": [100.0, 101.0, 102.0],
                "High": [101.0, 102.0, 103.0],
                "Low": [99.5, 100.5, 101.5],
                "Close": [100.5, 101.5, 102.5],
                "Volume": [1000.0, 1200.0, 1400.0],
            },
            index=pd.to_datetime(
                [
                    "2026-01-02T09:30:00Z",
                    "2026-01-02T09:35:00Z",
                    "2026-01-02T09:40:00Z",
                ]
            ),
        )

        market_service.clear_market_response_cache()
        with (
            patch.object(
                market_service,
                "make_settings",
                return_value=SimpleNamespace(period="5d", interval="5m"),
            ),
            patch.object(market_service.sdm, "download_ohlcv", return_value=frame),
            patch.object(
                market_service,
                "build_intraday_momentum_snapshot",
                return_value={"available": True, "overlays": {}},
            ),
        ):
            payload = market_service.get_chart_payload(
                ChartRequest(ticker="SPY", interval="5m", points_limit=50, regular_hours_only=True)
            )

        self.assertIn("freshness", payload)
        self.assertEqual(payload["freshness"]["ticker"], "SPY")
        self.assertEqual(payload["freshness"]["interval"], "5m")
        self.assertEqual(payload["freshness"]["point_count"], 3)
        self.assertIsNotNone(payload["freshness"]["latest_bar_at"])
        self.assertEqual(payload["freshness"]["session_policy"], "regular_hours_only")
        self.assertTrue(payload["regular_hours_only"])

    def test_ops_metrics_snapshot_tracks_latency_and_slow_requests(self) -> None:
        from backend.services import ops_service

        ops_service.reset_request_metrics()
        ops_service.record_request(
            path="/api/market/chart/SPY",
            method="GET",
            status_code=200,
            duration_seconds=0.145,
            request_id="req-chart",
        )
        ops_service.record_request(
            path="/api/frontend/bootstrap",
            method="GET",
            status_code=503,
            duration_seconds=1.25,
            request_id="req-bootstrap",
        )

        snapshot = ops_service.get_request_metrics_snapshot()

        self.assertEqual(snapshot["summary"]["total_requests"], 2)
        self.assertEqual(snapshot["summary"]["error_count"], 1)
        self.assertEqual(snapshot["summary"]["slow_request_count"], 1)
        self.assertGreater(snapshot["summary"]["p95_duration_ms"], 1000)
        self.assertEqual(snapshot["route_groups"][0]["key"], "market/chart")
        self.assertEqual(snapshot["recent_slow_requests"][0]["request_id"], "req-bootstrap")

    def test_ops_metrics_snapshot_tracks_timeout_risks(self) -> None:
        from backend.services import ops_service

        ops_service.reset_request_metrics()
        ops_service.record_request(
            path="/api/market/dashboard",
            method="GET",
            status_code=504,
            duration_seconds=5.8,
            request_id="req-timeout",
        )
        ops_service.record_request(
            path="/api/market/chart/SPY",
            method="GET",
            status_code=200,
            duration_seconds=0.19,
            request_id="req-fast",
        )

        snapshot = ops_service.get_request_metrics_snapshot()

        self.assertEqual(snapshot["summary"]["timeout_warning_count"], 1)
        self.assertEqual(snapshot["summary"]["timeout_warning_threshold_ms"], 5000)
        self.assertEqual(snapshot["recent_timeout_risks"][0]["request_id"], "req-timeout")

    def test_operation_metrics_snapshot_tracks_cache_and_slow_operations(self) -> None:
        from backend.services import ops_service

        ops_service.reset_operation_metrics()
        ops_service.record_operation(
            name="market.chart_payload",
            duration_seconds=0.08,
            cache_status="miss",
            context={"ticker": "SPY"},
        )
        ops_service.record_operation(
            name="market.chart_payload",
            duration_seconds=0.01,
            cache_status="hit",
            context={"ticker": "SPY"},
        )
        ops_service.record_operation(
            name="frontend.dashboard_snapshot",
            duration_seconds=1.35,
            cache_status="bypass",
            context={"tenant_slug": "alpha-desk"},
        )

        snapshot = ops_service.get_operation_metrics_snapshot()

        self.assertEqual(snapshot["summary"]["total_operations"], 3)
        self.assertEqual(snapshot["summary"]["cache_hit_count"], 1)
        self.assertEqual(snapshot["summary"]["cache_miss_count"], 1)
        self.assertEqual(snapshot["summary"]["cache_bypass_count"], 1)
        self.assertEqual(snapshot["operations"][0]["key"], "market.chart_payload")
        self.assertEqual(snapshot["recent_slow_operations"][0]["name"], "frontend.dashboard_snapshot")

    def test_route_profile_snapshot_tracks_stage_breakdown(self) -> None:
        from backend.services import ops_service

        ops_service.reset_route_profile_metrics()
        ops_service.record_route_profile(
            route_key="market.analyze",
            total_duration_seconds=1.42,
            context={"ticker": "SPY"},
            stages=[
                {"name": "download_history", "duration_ms": 410.0, "status": "ok"},
                {"name": "analyze_ticker", "duration_ms": 720.0, "status": "ok"},
                {"name": "serialize_payload", "duration_ms": 120.0, "status": "ok"},
            ],
        )
        ops_service.record_route_profile(
            route_key="market.analyze",
            total_duration_seconds=5.6,
            status="timeout",
            context={"ticker": "QQQ"},
            stages=[
                {"name": "download_history", "duration_ms": 5100.0, "status": "timeout"},
                {"name": "analyze_ticker", "duration_ms": 0.0, "status": "error"},
            ],
        )

        snapshot = ops_service.get_route_profile_snapshot()

        self.assertEqual(snapshot["summary"]["total_profiles"], 2)
        self.assertEqual(snapshot["summary"]["timeout_profile_count"], 1)
        self.assertEqual(snapshot["routes"][0]["key"], "market.analyze")
        self.assertEqual(snapshot["routes"][0]["stages"][0]["key"], "download_history")
        self.assertEqual(snapshot["recent_profiles"][0]["route_key"], "market.analyze")

    def test_upstream_metrics_snapshot_tracks_timeout_calls(self) -> None:
        from backend.services import ops_service

        ops_service.reset_upstream_metrics()
        ops_service.record_upstream_event(
            target="market-data",
            operation="download_ohlcv",
            duration_seconds=0.42,
            status="ok",
            context={"ticker": "SPY"},
        )
        ops_service.record_upstream_event(
            target="market-data",
            operation="get_live_price",
            duration_seconds=5.25,
            status="timeout",
            error_message="upstream timed out",
            context={"ticker": "SPY"},
        )

        snapshot = ops_service.get_upstream_metrics_snapshot()

        self.assertEqual(snapshot["summary"]["total_calls"], 2)
        self.assertEqual(snapshot["summary"]["timeout_count"], 1)
        self.assertEqual(snapshot["targets"][0]["key"], "market-data")
        self.assertEqual(snapshot["recent_timeouts"][0]["operation"], "get_live_price")

    def test_operations_status_includes_observability_snapshot(self) -> None:
        from backend.services import frontend_service

        request_metrics = {
            "window_size": 4,
            "lifetime_requests": 8,
            "lifetime_errors": 1,
            "started_at": "2026-04-16T10:00:00+00:00",
            "uptime_seconds": 600,
            "summary": {
                "total_requests": 4,
                "error_count": 1,
                "error_rate": 25.0,
                "average_duration_ms": 320.5,
                "p95_duration_ms": 870.2,
                "max_duration_ms": 1200.0,
                "slow_request_count": 1,
                "slow_request_threshold_ms": 800,
                "timeout_warning_count": 1,
                "timeout_warning_threshold_ms": 5000,
                "last_request_at": "2026-04-16T10:10:00+00:00",
            },
            "route_groups": [{"key": "market/chart", "count": 2}],
            "methods": [{"key": "GET", "count": 4}],
            "status_buckets": [{"key": "2xx", "count": 3}, {"key": "5xx", "count": 1}],
            "recent_slow_requests": [
                {
                    "request_id": "req-bootstrap",
                    "path": "/api/frontend/bootstrap",
                    "method": "GET",
                    "duration_ms": 1200.0,
                    "status_code": 503,
                    "at": "2026-04-16T10:10:00+00:00",
                }
            ],
            "recent_timeout_risks": [
                {
                    "request_id": "req-timeout",
                    "path": "/api/market/dashboard",
                    "method": "GET",
                    "duration_ms": 5800.0,
                    "status_code": 504,
                    "at": "2026-04-16T10:11:00+00:00",
                }
            ],
        }
        operation_metrics = {
            "window_size": 3,
            "lifetime_operations": 6,
            "lifetime_errors": 0,
            "started_at": "2026-04-16T10:00:00+00:00",
            "uptime_seconds": 600,
            "summary": {
                "total_operations": 3,
                "error_count": 0,
                "error_rate": 0.0,
                "timeout_count": 1,
                "average_duration_ms": 215.0,
                "p95_duration_ms": 1200.0,
                "max_duration_ms": 1200.0,
                "slow_operation_count": 1,
                "slow_operation_threshold_ms": 1200,
                "last_operation_at": "2026-04-16T10:10:00+00:00",
                "cache_hit_count": 1,
                "cache_miss_count": 1,
                "cache_bypass_count": 1,
            },
            "operations": [
                {
                    "key": "market.chart_payload",
                    "count": 2,
                    "average_duration_ms": 45.0,
                    "p95_duration_ms": 80.0,
                    "max_duration_ms": 80.0,
                    "cache_hits": 1,
                    "cache_misses": 1,
                    "cache_bypass": 0,
                    "error_count": 0,
                    "last_at": "2026-04-16T10:10:00+00:00",
                }
            ],
            "recent_slow_operations": [
                {
                    "name": "frontend.dashboard_snapshot",
                    "duration_ms": 1200.0,
                    "cache_status": "bypass",
                    "status": "ok",
                    "at": "2026-04-16T10:10:00+00:00",
                    "context": {"tenant_slug": "alpha-desk"},
                }
            ],
        }
        upstream_metrics = {
            "window_size": 3,
            "lifetime_calls": 8,
            "lifetime_timeouts": 1,
            "lifetime_errors": 0,
            "started_at": "2026-04-16T10:00:00+00:00",
            "uptime_seconds": 600,
            "summary": {
                "total_calls": 3,
                "timeout_count": 1,
                "error_count": 0,
                "error_rate": 0.0,
                "average_duration_ms": 311.2,
                "p95_duration_ms": 910.0,
                "max_duration_ms": 910.0,
                "last_call_at": "2026-04-16T10:10:30+00:00",
            },
            "targets": [{"key": "market-data", "count": 3, "timeout_count": 1}],
            "status_buckets": [{"key": "ok", "count": 2}, {"key": "timeout", "count": 1}],
            "recent_calls": [
                {
                    "target": "market-data",
                    "operation": "get_live_price",
                    "status": "timeout",
                    "duration_ms": 910.0,
                    "error_message": "upstream timed out",
                    "at": "2026-04-16T10:10:30+00:00",
                    "context": {"ticker": "SPY"},
                }
            ],
            "recent_timeouts": [
                {
                    "target": "market-data",
                    "operation": "get_live_price",
                    "status": "timeout",
                    "duration_ms": 910.0,
                    "error_message": "upstream timed out",
                    "at": "2026-04-16T10:10:30+00:00",
                    "context": {"ticker": "SPY"},
                }
            ],
        }
        route_profiles = {
            "window_size": 3,
            "lifetime_profiles": 8,
            "lifetime_slow_profiles": 2,
            "started_at": "2026-04-16T10:00:00+00:00",
            "uptime_seconds": 600,
            "summary": {
                "total_profiles": 3,
                "slow_profile_count": 1,
                "slow_profile_threshold_ms": 1200,
                "timeout_profile_count": 1,
                "average_total_duration_ms": 845.0,
                "p95_total_duration_ms": 2100.0,
                "max_total_duration_ms": 2100.0,
                "last_profile_at": "2026-04-16T10:10:40+00:00",
            },
            "routes": [
                {
                    "key": "market.analyze",
                    "count": 2,
                    "average_duration_ms": 920.0,
                    "p95_duration_ms": 2100.0,
                    "max_duration_ms": 2100.0,
                    "slow_count": 1,
                    "timeout_count": 1,
                    "last_at": "2026-04-16T10:10:40+00:00",
                    "stages": [
                        {
                            "key": "download_history",
                            "count": 2,
                            "timeout_count": 1,
                            "error_count": 0,
                            "average_duration_ms": 640.0,
                            "p95_duration_ms": 1300.0,
                            "max_duration_ms": 1300.0,
                        }
                    ],
                }
            ],
            "recent_profiles": [
                {
                    "route_key": "market.analyze",
                    "total_duration_ms": 2100.0,
                    "status": "timeout",
                    "at": "2026-04-16T10:10:40+00:00",
                    "context": {"ticker": "SPY"},
                    "stages": [],
                }
            ],
        }
        job_metrics = {
            "summary": {
                "count": 3,
                "queued": 1,
                "retrying": 1,
                "running": 0,
                "succeeded": 1,
                "dead_letter": 0,
                "pending": 2,
                "stuck_running_count": 0,
                "oldest_pending_at": "2026-04-16T10:05:00+00:00",
                "oldest_running_at": None,
                "running_stale_after_minutes": 10,
                "recent_failure_count": 1,
                "last_finished_at": "2026-04-16T10:09:00+00:00",
            },
            "job_types": [{"key": "partner_webhook_delivery", "label": "Partner webhook delivery", "count": 3}],
            "recent_jobs": [{"id": "job_123", "job_type": "partner_webhook_delivery", "job_label": "Partner webhook delivery", "status": "retrying", "attempt_count": 2, "max_attempts": 4, "available_at": "2026-04-16T10:11:00+00:00", "started_at": "2026-04-16T10:10:30+00:00", "finished_at": None, "error_message": "timeout", "last_http_status": 0, "tenant_id": "tenant_123"}],
            "recent_failures": [{"id": "job_123", "job_type": "partner_webhook_delivery", "status": "retrying", "attempt_count": 2, "max_attempts": 4, "available_at": "2026-04-16T10:11:00+00:00", "finished_at": None, "error_message": "timeout", "last_http_status": 0}],
            "stuck_running": [],
            "dead_letters": [],
        }
        worker_status = {
            "enabled": True,
            "running": True,
            "thread_name": "stock-signals-job-worker",
            "stop_requested": False,
            "poll_seconds": 5,
            "batch_size": 8,
            "last_loop_at": "2026-04-16T10:10:00+00:00",
            "last_success_at": "2026-04-16T10:09:55+00:00",
            "last_error_at": None,
            "last_error_message": None,
        }
        deployment_snapshot = {
            "summary": {
                "status": "attention",
                "readiness_percent": 83.3,
                "ready_checks": 10,
                "total_checks": 12,
                "blockers": ["Restore drill has not been recorded yet."],
                "next_action": "Restore drill has not been recorded yet.",
            },
            "deployment": {
                "items": [{"path": "docker-compose.yml", "label": "Docker Compose stack", "exists": True, "status": "ready", "modified_at": "2026-04-16T10:00:00+00:00"}],
                "count": 1,
                "ready_count": 1,
                "next_action": "Deployment artifacts are present.",
            },
            "backups": {
                "status": "attention",
                "provider": "local-volume-snapshot",
                "schedule": "nightly",
                "last_success_at": "2026-04-15T03:00:00+00:00",
                "last_attempt_at": "2026-04-15T03:00:00+00:00",
                "restore_tested_at": None,
                "retention_days": 14,
                "location": "backend-storage volume",
                "notes": "Restore drill pending.",
                "manifest_path": "runtime-logs/backup-status.json",
                "configured": True,
                "needs_attention": True,
                "checklist": [],
            },
            "runbooks": {
                "items": [{"path": "docs/runbooks/deployment.md", "label": "Deployment runbook", "exists": True, "status": "ready", "modified_at": "2026-04-16T10:00:00+00:00"}],
                "count": 1,
                "ready_count": 1,
                "next_action": "Runbooks are available.",
            },
        }
        market_data = {
            "ticker": "SPY",
            "interval": "5m",
            "status": "warning",
            "warning": True,
            "stale": False,
            "feed_expected": True,
            "session": "regular",
            "session_label": "Regular",
            "latest_bar_at": "2026-04-16T10:00:00+00:00",
            "latest_bar_age_seconds": 1800,
            "latest_bar_age_minutes": 30.0,
            "warning_threshold_seconds": 900,
            "stale_threshold_seconds": 1800,
            "point_count": 120,
            "source": "probe",
            "checked_at": "2026-04-16T10:10:00+00:00",
            "checked_at_et": "2026-04-16T06:10:00-04:00",
            "message": "Latest 5m bar for SPY is 30.0 minutes old and may indicate a lagging feed.",
        }
        release_gates = {
            "summary": {
                "status": "blocked",
                "ready": False,
                "checked_at": "2026-04-16T10:10:00+00:00",
                "ready_gates": 0,
                "warning_gates": 1,
                "blocked_gates": 2,
                "total_gates": 3,
                "blockers": [
                    "Stripe billing has recent failed webhook processing that should be reviewed.",
                    "Dead-letter jobs are present and should be cleared before pilot launch.",
                ],
                "warnings": ["Async job backlog is above the pilot warning threshold."],
                "next_action": "Stripe billing has recent failed webhook processing that should be reviewed.",
            },
            "gates": [
                {"key": "billing_sync", "label": "Billing sync", "status": "blocked", "passed": False, "blocking": True, "message": "Stripe billing has recent failed webhook processing that should be reviewed.", "details": {}},
                {"key": "job_backlog", "label": "Job backlog", "status": "warning", "passed": False, "blocking": False, "message": "Async job backlog is above the pilot warning threshold.", "details": {}},
                {"key": "dead_letters", "label": "Dead letters", "status": "blocked", "passed": False, "blocking": True, "message": "Dead-letter jobs are present and should be cleared before pilot launch.", "details": {}},
            ],
            "tenant": {"slug": "alpha-desk", "name": "Alpha Desk", "status": "active", "plan_key": "pro"},
        }
        auth_config = {
            "mode": "configured",
            "provider": "auth0",
            "available_providers": [
                {"key": "local-session", "enabled": True, "ready": True, "mode": "form"},
                {"key": "auth0", "enabled": True, "ready": True, "mode": "redirect"},
            ],
        }
        billing_ops = {
            "tenant": {"slug": "alpha-desk", "name": "Alpha Desk", "status": "active", "plan_key": "pro", "provider": "stripe"},
            "summary": {
                "status": "attention",
                "message": "Billing recovery activity needs review.",
                "needs_attention": True,
                "pending_job_count": 1,
                "failed_event_count": 1,
                "drill_count": 3,
                "replay_count": 1,
                "last_drill_at": "2026-04-16T10:05:00+00:00",
                "last_replay_at": "2026-04-16T10:04:00+00:00",
            },
            "sync": {
                "status": "attention",
                "message": "Recent billing failure detected.",
                "provider": "stripe",
                "last_event_key": "checkout.session.completed",
                "last_event_at": "2026-04-16T10:03:00+00:00",
                "last_processed_at": "2026-04-16T10:04:00+00:00",
                "last_failed_at": "2026-04-16T10:02:00+00:00",
                "recent_failure_count": 1,
                "duplicate_count": 0,
                "needs_reconciliation": True,
                "available_actions": ["reconcile", "retry_last_failure"],
            },
            "recovery": {
                "enabled": True,
                "last_reconciled_at": "2026-04-16T10:05:00+00:00",
                "last_recovery_action": "retry_last_failure",
                "last_recovery_status": "succeeded",
                "last_recovery_error": None,
                "latest_failed_event_id": "bevt_failed_1",
                "latest_failed_event_at": "2026-04-16T10:02:00+00:00",
                "pending_job_count": 1,
                "failed_event_count": 1,
            },
            "drills": {
                "items": [
                    {"id": "bevt_replay", "action": "retry_last_failure", "kind": "replay", "status": "processed", "completed_at": "2026-04-16T10:04:00+00:00", "started_at": "2026-04-16T10:03:30+00:00"},
                ],
                "count": 3,
                "replay_count": 1,
                "last_drill_at": "2026-04-16T10:05:00+00:00",
                "last_replay_at": "2026-04-16T10:04:00+00:00",
            },
            "recent_jobs": [],
            "failed_events": [{"id": "bevt_failed_1", "event_key": "checkout.session.completed", "processed_at": "2026-04-16T10:02:00+00:00", "received_at": "2026-04-16T10:02:00+00:00", "external_event_id": "evt_failed_1"}],
            "events": {"count": 5, "status_counts": {"failed": 1, "processed": 3, "duplicate": 1}},
        }
        rate_limits = {
            "summary": {
                "enabled": True,
                "throttle_event_count": 2,
                "blocked_actor_count": 1,
                "auth_lockout_count": 1,
                "abuse_failure_count": 3,
                "last_throttle_at": "2026-04-16T10:08:00Z",
                "last_abuse_event_at": "2026-04-16T10:09:00Z",
            },
            "recent_events": [
                {
                    "policy_key": "tenant.market.read",
                    "policy_label": "Tenant market traffic",
                    "bucket": "tenant:alpha-desk",
                    "retry_after_seconds": 45,
                    "at": "2026-04-16T10:08:00Z",
                }
            ],
            "recent_abuse": [
                {
                    "event_type": "auth.login.failure",
                    "actor_key": "login:email:ops@alpha.test",
                    "at": "2026-04-16T10:09:00Z",
                }
            ],
            "blocked_actors": [
                {
                    "actor_key": "login:email:ops@alpha.test",
                    "blocked_until": "2026-04-16T10:20:00Z",
                    "reason": "auth_failures",
                }
            ],
        }
        order_lifecycle = {
            "summary": {
                "status": "warning",
                "message": "Order lifecycle needs attention: stale working orders or rejects are present.",
                "pending_order_count": 2,
                "stale_pending_count": 1,
                "reject_count": 1,
                "fill_count": 2,
                "closed_count": 1,
                "last_event_at": "2026-04-16T10:09:00+00:00",
                "last_reject_at": "2026-04-16T10:04:00+00:00",
                "last_fill_at": "2026-04-16T10:09:00+00:00",
            },
            "checks": [
                {"key": "pending_orders", "label": "Pending orders", "status": "warning", "count": 2, "message": "Pending orders include stale working tickets."},
            ],
            "stale_pending_orders": [
                {"order_id": "ord_1", "ticker": "SPY", "order_type": "limit", "age_minutes": 1600.0, "stale_after_minutes": 1440},
            ],
            "recent_rejections": [
                {"id": "ord_evt_reject", "ticker": "SPY", "order_type": "limit", "detail": "Rejected by route guard.", "created_at": "2026-04-16T10:04:00+00:00"},
            ],
            "recent_fills": [
                {"id": "ord_evt_fill", "ticker": "QQQ", "order_type": "market", "label": "Filled", "created_at": "2026-04-16T10:09:00+00:00"},
            ],
            "recent_closed": [],
        }
        launch_rollup = {
            "tenant": {"slug": "alpha-desk", "name": "Alpha Desk", "status": "active", "plan_key": "pro"},
            "summary": {
                "status": "blocked",
                "enabled": True,
                "stage": "Launch blocked",
                "launch_ready": False,
                "release_channel": "pilot",
                "blocker_count": 2,
                "completed_checks": 1,
                "total_checks": 4,
                "last_ready_at": None,
                "last_failed_at": "2026-04-16T10:06:00+00:00",
                "next_action": "Primary domain routing must be live before activating this tenant.",
            },
            "checks": {
                "domain_required": True,
                "domain_ready": False,
                "sender_required": True,
                "sender_ready": True,
                "auth_required": True,
                "auth_ready": False,
            },
            "checklist": [
                {"key": "launch_review", "label": "Launch review approved", "complete": True, "detail": "Manual approval checkpoint before launch."},
                {"key": "domain_live", "label": "Primary domain live", "complete": False, "detail": "Current status: verified"},
                {"key": "sender_live", "label": "Branded sender live", "complete": True, "detail": "Provider Resend"},
                {"key": "auth_routing", "label": "Tenant SSO launch-ready", "complete": False, "detail": "Validate tenant auth routing."},
            ],
            "blockers": [
                "Primary domain routing must be live before activating this tenant.",
                "Tenant SSO routing is not launch-ready yet.",
            ],
            "recent_operations": [
                {"key": "provider_auth0_acme", "label": "Acme Workforce", "status": "error", "at": "2026-04-16T10:06:00+00:00"},
            ],
        }
        readiness_snapshot = {
            "summary": {
                "status": "blocked",
                "ready": False,
                "checked_at": "2026-04-16T10:10:00+00:00",
                "ready_checks": 2,
                "warning_checks": 1,
                "blocked_checks": 2,
                "total_checks": 5,
                "readiness_percent": 40.0,
                "blockers": ["Deployment artifacts are incomplete."],
                "warnings": ["Tenant billing needs attention."],
                "next_action": "Deployment artifacts are incomplete.",
            },
            "checks": [
                {
                    "key": "database",
                    "label": "Database connectivity",
                    "status": "ready",
                    "ready": True,
                    "message": "Database probe succeeded.",
                    "details": {},
                }
            ],
            "tenant": {"slug": "alpha-desk", "name": "Alpha Desk", "status": "active", "plan_key": "pro"},
        }

        with ExitStack() as stack:
            for patcher in (
                patch.object(frontend_service, "get_health", return_value={"status": "ok", "timestamp": "2026-04-16T10:10:00+00:00"}),
                patch.object(frontend_service, "get_release_info", return_value={"version": "2.6.0", "phase": "release-candidate"}),
                patch.object(frontend_service, "get_alerts_snapshot", return_value={"alerts": [], "count": 0, "total": 0}),
                patch.object(frontend_service, "list_workspaces", return_value={"items": [], "count": 0}),
                patch.object(frontend_service, "get_ticker_hub", return_value={"favorites": [], "recents": [], "favorite_count": 0, "recent_count": 0}),
                patch.object(frontend_service, "get_notes_summary", return_value={"active_count": 0, "overdue_count": 0, "high_priority_count": 0}),
                patch.object(frontend_service, "get_portfolio", return_value={"summary": {"open_trade_count": 0, "total_realized_pnl": 0, "win_rate": 0, "profit_factor": 0}}),
                patch.object(frontend_service, "drain_due_jobs", return_value={"claimed": 1, "succeeded": 1, "retried": 0, "dead_letter": 0}),
                patch.object(frontend_service, "get_request_metrics_snapshot", return_value=request_metrics),
                patch.object(frontend_service, "get_operation_metrics_snapshot", return_value=operation_metrics),
                patch.object(frontend_service, "get_route_profile_snapshot", return_value=route_profiles),
                patch.object(frontend_service, "get_upstream_metrics_snapshot", return_value=upstream_metrics),
                patch.object(frontend_service, "get_job_metrics_snapshot", return_value=job_metrics),
                patch.object(frontend_service, "get_job_worker_status", return_value=worker_status),
                patch.object(frontend_service, "get_deployment_readiness_snapshot", return_value=deployment_snapshot),
                patch.object(frontend_service, "get_market_data_freshness_snapshot", return_value=market_data),
                patch.object(frontend_service, "get_production_readiness_snapshot", return_value=readiness_snapshot),
                patch.object(frontend_service, "get_release_gate_snapshot", return_value=release_gates),
                patch.object(frontend_service, "get_auth_provider_config", return_value=auth_config),
                patch.object(frontend_service, "get_billing_ops_snapshot", return_value=billing_ops),
                patch.object(frontend_service, "get_rate_limit_snapshot", return_value=rate_limits),
                patch.object(frontend_service, "get_order_lifecycle_health_snapshot", return_value=order_lifecycle),
                patch.object(frontend_service, "get_tenant_launch_rollup", return_value=launch_rollup),
            ):
                stack.enter_context(patcher)
            payload = frontend_service.get_operations_status("demo-trader", tenant_slug="alpha-desk")

        self.assertEqual(payload["observability"]["requests"]["summary"]["average_duration_ms"], 320.5)
        self.assertEqual(payload["observability"]["operations"]["summary"]["cache_hit_count"], 1)
        self.assertEqual(payload["observability"]["operations"]["recent_slow_operations"][0]["name"], "frontend.dashboard_snapshot")
        self.assertEqual(payload["observability"]["route_profiles"]["summary"]["total_profiles"], 3)
        self.assertEqual(payload["observability"]["route_profiles"]["routes"][0]["key"], "market.analyze")
        self.assertEqual(payload["observability"]["requests"]["route_groups"][0]["key"], "market/chart")
        self.assertEqual(payload["observability"]["requests"]["recent_slow_requests"][0]["request_id"], "req-bootstrap")
        self.assertEqual(payload["observability"]["requests"]["summary"]["timeout_warning_count"], 1)
        self.assertEqual(payload["observability"]["requests"]["recent_timeout_risks"][0]["request_id"], "req-timeout")
        self.assertEqual(payload["observability"]["upstream"]["summary"]["timeout_count"], 1)
        self.assertEqual(payload["observability"]["upstream"]["recent_timeouts"][0]["operation"], "get_live_price")
        self.assertEqual(payload["observability"]["jobs"]["summary"]["queued"], 1)
        self.assertEqual(payload["observability"]["jobs"]["recent_jobs"][0]["id"], "job_123")
        self.assertTrue(payload["observability"]["jobs"]["worker"]["running"])
        self.assertEqual(payload["market_data"]["status"], "warning")
        self.assertEqual(payload["market_data"]["latest_bar_age_minutes"], 30.0)
        self.assertEqual(payload["readiness"]["summary"]["status"], "blocked")
        self.assertEqual(payload["release_gates"]["summary"]["blocked_gates"], 2)
        self.assertEqual(payload["release_gates"]["gates"][0]["key"], "billing_sync")
        self.assertEqual(payload["readiness"]["tenant"]["slug"], "alpha-desk")
        self.assertEqual(payload["deployment"]["summary"]["readiness_percent"], 83.3)
        self.assertEqual(payload["deployment"]["backups"]["provider"], "local-volume-snapshot")
        self.assertEqual(payload["billing"]["summary"]["replay_count"], 1)
        self.assertEqual(payload["service_smoke"]["summary"]["status"], "blocked")
        self.assertEqual(payload["service_smoke"]["summary"]["blocked_checks"], 1)
        self.assertEqual(payload["service_smoke"]["checks"][0]["key"], "auth")
        self.assertEqual(payload["billing"]["recovery"]["last_recovery_action"], "retry_last_failure")
        self.assertEqual(payload["rate_limits"]["summary"]["throttle_event_count"], 2)
        self.assertEqual(payload["rate_limits"]["blocked_actors"][0]["actor_key"], "login:email:ops@alpha.test")
        self.assertEqual(payload["orders"]["summary"]["stale_pending_count"], 1)
        self.assertEqual(payload["orders"]["recent_rejections"][0]["id"], "ord_evt_reject")
        self.assertEqual(payload["launch"]["summary"]["stage"], "Launch blocked")
        self.assertFalse(payload["launch"]["summary"]["launch_ready"])
        self.assertEqual(payload["launch"]["checklist"][1]["key"], "domain_live")

    def test_frontend_bootstrap_records_route_profile_stages(self) -> None:
        from backend.services import frontend_service, ops_service

        frontend_service.clear_frontend_snapshot_cache()
        ops_service.reset_route_profile_metrics()
        with (
            patch.object(frontend_service, "get_defaults", return_value={"default_scan_tickers": ["SPY"], "default_interval": "5m", "default_horizon": 5}),
            patch.object(frontend_service, "get_alerts_snapshot", return_value={"alerts": [], "count": 0, "total": 0}),
            patch.object(frontend_service, "list_workspaces", return_value={"items": [], "count": 0}),
            patch.object(frontend_service, "get_ticker_hub", return_value={"favorites": [], "recents": [], "favorite_count": 0, "recent_count": 0}),
        ):
            frontend_service.get_frontend_bootstrap("demo-trader", tenant_slug="alpha-desk")

        snapshot = ops_service.get_route_profile_snapshot()

        self.assertEqual(snapshot["routes"][0]["key"], "frontend.bootstrap")
        stage_keys = [item["key"] for item in snapshot["routes"][0]["stages"]]
        self.assertIn("defaults", stage_keys)
        self.assertIn("workspaces", stage_keys)

    def test_frontend_bootstrap_shell_consumer_trims_unused_sections(self) -> None:
        from backend.services import frontend_service

        frontend_service.clear_frontend_snapshot_cache()
        with (
            patch.object(
                frontend_service,
                "get_defaults",
                return_value={
                    "default_scan_tickers": ["SPY", "QQQ"],
                    "supported_intervals": ["1m", "5m"],
                    "default_interval": "5m",
                    "default_horizon": 5,
                    "live_update_seconds": 15,
                },
            ),
            patch.object(frontend_service, "get_alerts_snapshot", side_effect=AssertionError("shell consumer should not fetch alerts")),
            patch.object(frontend_service, "list_workspaces", side_effect=AssertionError("shell consumer should not fetch workspaces")),
            patch.object(frontend_service, "get_ticker_hub", side_effect=AssertionError("shell consumer should not fetch ticker hub")),
        ):
            payload = frontend_service.get_frontend_bootstrap("demo-trader", tenant_slug="alpha-desk", consumer="shell")

        self.assertIn("app", payload)
        self.assertIn("defaults", payload)
        self.assertNotIn("alerts", payload)
        self.assertNotIn("presets", payload)
        self.assertNotIn("workspace_count", payload)
        self.assertNotIn("ticker_hub", payload)
        self.assertNotIn("watchlist_preview", payload)

    def test_frontend_bootstrap_watchlist_consumer_includes_preview_board(self) -> None:
        from backend.services import frontend_service

        frontend_service.clear_frontend_snapshot_cache()
        with (
            patch.object(
                frontend_service,
                "get_defaults",
                return_value={
                    "default_scan_tickers": ["SPY", "QQQ", "AAPL", "MSFT"],
                    "supported_intervals": ["1m", "5m"],
                    "default_interval": "5m",
                    "default_horizon": 5,
                    "live_update_seconds": 15,
                },
            ),
        ):
            payload = frontend_service.get_frontend_bootstrap("demo-trader", tenant_slug="alpha-desk", consumer="watchlist")

        self.assertIn("watchlist_preview", payload)
        self.assertEqual(payload["watchlist_preview"]["count"], 4)
        self.assertEqual(payload["watchlist_preview"]["rows"][0]["ticker"], "SPY")
        self.assertEqual(payload["watchlist_preview"]["summary"]["ranking_board"]["leader"]["ticker"], "SPY")

    def test_frontend_bootstrap_watchlist_consumer_triggers_market_prefetch(self) -> None:
        from backend.services import frontend_service

        frontend_service.clear_frontend_snapshot_cache()
        with (
            patch.object(
                frontend_service,
                "get_defaults",
                return_value={
                    "default_scan_tickers": ["SPY", "QQQ", "AAPL", "MSFT"],
                    "supported_intervals": ["1m", "5m"],
                    "default_interval": "5m",
                    "default_horizon": 5,
                    "live_update_seconds": 15,
                },
            ),
            patch.object(frontend_service, "_prefetch_watchlist_market_data") as prefetch_mock,
        ):
            frontend_service.get_frontend_bootstrap("demo-trader", tenant_slug="alpha-desk", consumer="watchlist")

        prefetch_mock.assert_called_once()

    def test_dashboard_snapshot_desk_consumer_trims_unused_sections(self) -> None:
        from backend.services import frontend_service

        with (
            patch.object(
                frontend_service,
                "get_defaults",
                return_value={
                    "default_scan_tickers": ["SPY", "QQQ", "AAPL"],
                    "default_interval": "5m",
                    "default_horizon": 5,
                },
            ),
            patch.object(
                frontend_service,
                "get_portfolio_dashboard_snapshot",
                return_value={
                    "summary": {"open_trade_count": 1},
                    "open_trades": [],
                    "pending_orders": [],
                    "monitored_open_trades": [],
                    "order_events": {"items": [], "count": 0, "status_counts": {}},
                },
            ),
            patch.object(
                frontend_service,
                "list_notes",
                side_effect=[
                    {
                        "items": [
                            {
                                "id": "note-review-1",
                                "title": "Review execution drift on NVDA open",
                                "ticker": "NVDA",
                                "priority": "high",
                                "blocked_state": "ready",
                                "progress_state": "planned",
                                "updated_at": "2026-04-18T09:15:00+00:00",
                            }
                        ],
                        "count": 1,
                        "total": 1,
                        "tags": [{"tag": "review-loop", "count": 1}],
                        "tickers": [{"ticker": "NVDA", "count": 1}],
                        "owners": [{"owner": "journal", "count": 1}],
                    },
                    {
                        "items": [
                            {
                                "id": "note-review-done-1",
                                "title": "Resolved sizing review on NVDA",
                                "ticker": "NVDA",
                                "priority": "medium",
                                "completed": True,
                                "updated_at": "2026-04-17T15:45:00+00:00",
                            }
                        ],
                        "count": 1,
                        "total": 3,
                        "tags": [{"tag": "review-loop", "count": 3}],
                        "tickers": [{"ticker": "NVDA", "count": 3}],
                        "owners": [{"owner": "journal", "count": 2}],
                    },
                ],
            ),
            patch.object(
                frontend_service,
                "run_scan",
                return_value={"interval": "5m", "horizon": 5, "tickers_requested": ["SPY"], "result_count": 0, "results": [], "errors": []},
            ),
            patch.object(
                frontend_service,
                "build_watchlist_from_scan_payload",
                return_value={"summary": {"valid_trades": 0, "high_conviction": 0, "entry_now": 0}, "rows": [], "results": [], "count": 0, "errors": []},
            ),
            patch.object(
                frontend_service,
                "build_event_calendar_snapshot",
                return_value={
                    "count": 1,
                    "total": 2,
                    "items": [
                        {
                            "key": "macro:FOMC:2026-05-06",
                            "source": "macro_calendar",
                            "title": "FOMC",
                            "event_date": "2026-05-06",
                            "days_until": 18,
                            "tone": "warning",
                        }
                    ],
                    "summary": {"macro_count": 1, "ticker_count": 0, "high_impact_count": 1, "caution_count": 1, "next_item": {"title": "FOMC"}},
                },
            ),
            patch.object(frontend_service, "get_health", side_effect=AssertionError("desk consumer should not call health")),
        ):
            payload = frontend_service.get_dashboard_snapshot(consumer="desk")

        self.assertIn("scan", payload)
        self.assertIn("watchlist", payload)
        self.assertIn("event_calendar", payload)
        self.assertIn("portfolio", payload)
        self.assertIn("review_loop_notes", payload)
        self.assertIn("review_loop_progress", payload)
        self.assertEqual(payload["event_calendar"]["count"], 1)
        self.assertEqual(payload["event_calendar"]["items"][0]["title"], "FOMC")
        self.assertEqual(payload["review_loop_notes"]["count"], 1)
        self.assertEqual(payload["review_loop_notes"]["items"][0]["ticker"], "NVDA")
        self.assertEqual(payload["review_loop_progress"]["open_count"], 1)
        self.assertEqual(payload["review_loop_progress"]["resolved_count"], 3)
        self.assertEqual(payload["review_loop_progress"]["latest_resolved"]["title"], "Resolved sizing review on NVDA")
        self.assertNotIn("health", payload)
        self.assertNotIn("defaults", payload)

    def test_event_calendar_snapshot_combines_macro_and_watchlist_events(self) -> None:
        from backend.services import event_calendar_service

        with patch.object(
            event_calendar_service,
            "load_macro_events",
            return_value=[
                {
                    "key": "macro:FOMC:2026-05-06",
                    "source": "macro_calendar",
                    "title": "FOMC",
                    "event_date": "2026-05-06",
                    "days_until": 18,
                    "impact": "high",
                    "tone": "warning",
                    "label": "Macro window",
                    "detail": "FOMC is scheduled for 2026-05-06.",
                }
            ],
        ):
            payload = event_calendar_service.build_event_calendar_snapshot(
                watchlist_rows=[
                    {
                        "ticker": "NVDA",
                        "next_event_name": "NVDA Earnings",
                        "next_event_date": "2026-05-28",
                        "next_event_days": 40,
                        "ranking_score": 82.4,
                        "ranking_tier": "promote",
                        "event_context": {
                            "trade_posture": "caution",
                            "event_severity": "medium",
                            "event_window_label": "earnings_window",
                            "summary": "Earnings are close enough to keep the setup conditional.",
                        },
                    }
                ],
                limit=4,
            )

        self.assertEqual(payload["count"], 2)
        self.assertEqual(payload["summary"]["macro_count"], 1)
        self.assertEqual(payload["summary"]["ticker_count"], 1)
        self.assertEqual(payload["items"][0]["title"], "FOMC")
        self.assertEqual(payload["items"][1]["ticker"], "NVDA")
        self.assertEqual(payload["items"][1]["label"], "Earnings window")

    def test_market_upstream_timeout_is_recorded_in_metrics(self) -> None:
        from backend.services import market_service, ops_service

        ops_service.reset_upstream_metrics()
        with patch.object(market_service.sdm, "get_live_price", side_effect=TimeoutError("Market data timed out")):
            with self.assertRaises(TimeoutError):
                market_service.get_live_price_snapshot("SPY")

        snapshot = ops_service.get_upstream_metrics_snapshot()

        self.assertEqual(snapshot["summary"]["timeout_count"], 1)
        self.assertEqual(snapshot["recent_timeouts"][0]["target"], "market-data")
        self.assertEqual(snapshot["recent_timeouts"][0]["operation"], "get_live_price")

    def test_compare_route_profile_records_profile_breakdown(self) -> None:
        from backend.services import market_service, ops_service

        frame = pd.DataFrame(
            {
                "Open": [100.0, 101.0],
                "High": [102.0, 103.0],
                "Low": [99.0, 100.0],
                "Close": [101.0, 102.0],
                "Volume": [1000, 1100],
            },
            index=pd.date_range("2026-01-01", periods=2, freq="D"),
        )
        report = {
            "ticker": "SPY",
            "verdict": "BULLISH",
            "probability_up": 0.72,
            "setup_score": 81.0,
            "setup_grade": "A setup",
            "conviction_label": "HIGH CONVICTION",
            "alignment_label": "Aligned",
            "trade_decision": "VALID TRADE",
            "reject_reason": "",
            "close": 102.0,
            "option_plan": {
                "option_side": "CALL",
                "entry_low_price": 101.0,
                "entry_high_price": 103.0,
                "expected_underlying_target": 108.0,
                "stop_loss": 0.35,
                "recommended_contract": {"contract_symbol": "SPY260101C00102000"},
            },
        }

        market_service.clear_market_response_cache()
        ops_service.reset_route_profile_metrics()
        with (
            patch.object(market_service.sdm, "batch_download_ohlcv", return_value={"SPY": frame, "QQQ": frame}),
            patch.object(market_service.sdm, "batch_get_live_prices", return_value={"SPY": 102.0, "QQQ": 102.0}),
            patch.object(market_service.sdm, "analyze_ticker", return_value=report),
            patch.object(market_service.sdm, "get_execution_decision", return_value="BUY NOW"),
            patch.object(market_service.sdm, "evaluate_trade_status", return_value="ENTER NOW"),
            patch.object(market_service, "get_chart_payload", return_value={"candles": [], "overlays": {}}),
        ):
            market_service.compare_tickers(CompareRequest(tickers=["SPY", "QQQ"]))

        snapshot = ops_service.get_route_profile_snapshot()

        self.assertEqual(snapshot["routes"][0]["key"], "market.compare")
        compare_stage_keys = [item["key"] for item in snapshot["routes"][0]["stages"]]
        self.assertIn("batch_download_history", compare_stage_keys)
        self.assertIn("per_ticker_compare", compare_stage_keys)

    def test_production_readiness_snapshot_rolls_up_worker_backlog_and_tenant_state(self) -> None:
        from backend.core.database import Base
        from backend.models.saas import BillingEventRecord, SubscriptionRecord, Tenant
        from backend.services import readiness_service

        engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
        Session = sessionmaker(bind=engine, expire_on_commit=False, future=True)
        Base.metadata.create_all(bind=engine)

        with Session() as session:
            tenant = Tenant(
                slug="alpha-desk",
                name="Alpha Desk",
                plan_key="pro",
                status="active",
                metadata_json={
                    "delivery": {
                        "primary_domain": "desk.example.com",
                        "domain_status": "live",
                        "sender_email": "ops@desk.example.com",
                        "provider_status": "test_sent",
                        "release_channel": "stable",
                        "auth_policy": "prefer_sso",
                        "enabled_providers": ["auth0"],
                        "provider_records": [
                            {
                                "id": "provider_1",
                                "provider_key": "auth0",
                                "enabled": True,
                                "health_status": "ready",
                                "has_client_secret": True,
                                "is_default": True,
                            }
                        ],
                    },
                    "onboarding": {
                        "completed_steps": ["launch_review"],
                        "steps": [
                            {"key": "branding", "completed": True},
                            {"key": "billing", "completed": True},
                            {"key": "launch_review", "completed": True},
                        ],
                    },
                },
            )
            session.add(tenant)
            session.flush()

            subscription = SubscriptionRecord(
                tenant_id=tenant.id,
                plan_key="pro",
                status="active",
                provider="stripe",
                external_customer_id="cus_ready",
                external_subscription_id="sub_ready",
                metadata_json={},
            )
            session.add(subscription)
            session.add(
                BillingEventRecord(
                    tenant_id=tenant.id,
                    provider="stripe",
                    event_key="customer.subscription.updated",
                    status="failed",
                    external_event_id="evt_failed",
                    plan_key="pro",
                    payload_json={},
                    result_json={},
                )
            )
            session.commit()

            job_metrics = {
                "summary": {
                    "count": 2,
                    "queued": 0,
                    "retrying": 0,
                    "running": 0,
                    "succeeded": 1,
                    "dead_letter": 1,
                    "pending": 1,
                    "oldest_pending_at": "2026-04-16T09:00:00+00:00",
                    "recent_failure_count": 1,
                    "last_finished_at": "2026-04-16T10:00:00+00:00",
                },
                "job_types": [],
                "recent_jobs": [],
                "recent_failures": [],
                "dead_letters": [{"id": "job_dead"}],
            }
            worker_status = {
                "enabled": True,
                "running": True,
                "thread_name": "stock-signals-job-worker",
                "stop_requested": False,
                "poll_seconds": 5,
                "batch_size": 8,
                "last_loop_at": "2026-04-16T10:09:00+00:00",
                "last_success_at": "2026-04-16T10:09:00+00:00",
                "last_error_at": None,
                "last_error_message": None,
            }
            deployment_snapshot = {
                "summary": {
                    "status": "attention",
                    "readiness_percent": 75.0,
                    "ready_checks": 9,
                    "total_checks": 12,
                    "blockers": ["Restore drill has not been recorded yet."],
                    "next_action": "Restore drill has not been recorded yet.",
                },
                "deployment": {"items": [], "count": 0, "ready_count": 0, "next_action": "Restore deployment artifacts."},
                "backups": {"status": "attention", "checklist": []},
                "runbooks": {"items": [], "count": 0, "ready_count": 0, "next_action": "Finish the missing operator runbooks."},
            }

            with (
                patch.object(readiness_service, "get_job_metrics_snapshot", return_value=job_metrics),
                patch.object(readiness_service, "get_job_worker_status", return_value=worker_status),
                patch.object(readiness_service, "get_deployment_readiness_snapshot", return_value=deployment_snapshot),
            ):
                snapshot = readiness_service.get_production_readiness_snapshot(
                    db=session,
                    tenant_slug="alpha-desk",
                )

        self.assertEqual(snapshot["summary"]["status"], "blocked")
        self.assertIn("Deployment readiness", {item["label"] for item in snapshot["checks"]})
        self.assertEqual(snapshot["summary"]["blocked_checks"], 3)
        self.assertEqual(snapshot["summary"]["warning_checks"], 1)
        self.assertEqual(snapshot["tenant"]["slug"], "alpha-desk")
        self.assertIn("Stripe billing", " ".join(snapshot["summary"]["warnings"]))
        self.assertIn("Branded sender routing must be live", " ".join(snapshot["summary"]["blockers"]))

    def test_production_readiness_snapshot_accepts_naive_pending_timestamps(self) -> None:
        from backend.core.database import Base
        from backend.services import readiness_service

        engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
        Session = sessionmaker(bind=engine, expire_on_commit=False, future=True)
        Base.metadata.create_all(bind=engine)

        with Session() as session:
            job_metrics = {
                "summary": {
                    "count": 1,
                    "queued": 1,
                    "retrying": 0,
                    "running": 0,
                    "succeeded": 0,
                    "dead_letter": 0,
                    "pending": 1,
                    "oldest_pending_at": "2026-04-16T09:00:00",
                    "recent_failure_count": 0,
                    "last_finished_at": None,
                },
                "job_types": [],
                "recent_jobs": [],
                "recent_failures": [],
                "dead_letters": [],
            }
            worker_status = {
                "enabled": True,
                "running": True,
                "thread_name": "stock-signals-job-worker",
                "stop_requested": False,
                "poll_seconds": 5,
                "batch_size": 8,
                "last_loop_at": "2026-04-16T10:09:00+00:00",
                "last_success_at": "2026-04-16T10:09:00+00:00",
                "last_error_at": None,
                "last_error_message": None,
            }
            deployment_snapshot = {
                "summary": {
                    "status": "ready",
                    "readiness_percent": 100.0,
                    "ready_checks": 12,
                    "total_checks": 12,
                    "blockers": [],
                    "next_action": "Pilot production checks are ready.",
                }
            }

            with (
                patch.object(readiness_service, "_utc_now", return_value=datetime(2026, 4, 16, 10, 0, tzinfo=timezone.utc)),
                patch.object(readiness_service, "get_job_metrics_snapshot", return_value=job_metrics),
                patch.object(readiness_service, "get_job_worker_status", return_value=worker_status),
                patch.object(readiness_service, "get_deployment_readiness_snapshot", return_value=deployment_snapshot),
            ):
                snapshot = readiness_service.get_production_readiness_snapshot(db=session)

        self.assertEqual(snapshot["summary"]["status"], "warning")
        self.assertIn("Async job backlog is growing or stale.", " ".join(snapshot["summary"]["warnings"]))

    def test_release_gate_snapshot_blocks_on_billing_backlog_and_dead_letters(self) -> None:
        from backend.core.database import Base
        from backend.models.saas import BillingEventRecord, SubscriptionRecord, Tenant
        from backend.services import readiness_service

        engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
        Session = sessionmaker(bind=engine, expire_on_commit=False, future=True)
        Base.metadata.create_all(bind=engine)

        with Session() as session:
            tenant = Tenant(
                slug="alpha-desk",
                name="Alpha Desk",
                plan_key="pro",
                status="active",
                metadata_json={},
            )
            session.add(tenant)
            session.flush()

            subscription = SubscriptionRecord(
                tenant_id=tenant.id,
                plan_key="pro",
                status="active",
                provider="stripe",
                external_customer_id="cus_gate",
                external_subscription_id="sub_gate",
                metadata_json={},
            )
            session.add(subscription)
            session.add(
                BillingEventRecord(
                    tenant_id=tenant.id,
                    provider="stripe",
                    event_key="customer.subscription.updated",
                    status="failed",
                    external_event_id="evt_gate_failed",
                    plan_key="pro",
                    payload_json={},
                    result_json={},
                )
            )
            session.commit()

            job_metrics = {
                "summary": {
                    "count": 4,
                    "queued": 8,
                    "retrying": 1,
                    "running": 0,
                    "succeeded": 1,
                    "dead_letter": 2,
                    "pending": 12,
                    "stuck_running_count": 0,
                    "oldest_pending_at": "2026-04-16T09:00:00+00:00",
                    "oldest_running_at": None,
                    "running_stale_after_minutes": 10,
                    "recent_failure_count": 2,
                    "last_finished_at": "2026-04-16T10:00:00+00:00",
                },
                "job_types": [],
                "recent_jobs": [],
                "recent_failures": [],
                "stuck_running": [],
                "dead_letters": [{"id": "job_dead_1"}, {"id": "job_dead_2"}],
            }

            with patch.object(readiness_service, "get_job_metrics_snapshot", return_value=job_metrics):
                snapshot = readiness_service.get_release_gate_snapshot(
                    db=session,
                    tenant_slug="alpha-desk",
                )

        self.assertEqual(snapshot["summary"]["status"], "blocked")
        self.assertEqual(snapshot["summary"]["blocked_gates"], 3)
        self.assertEqual(snapshot["summary"]["warning_gates"], 0)
        self.assertEqual(snapshot["gates"][0]["key"], "billing_sync")
        self.assertEqual(snapshot["gates"][0]["status"], "blocked")
        self.assertIn("Async job backlog", " ".join(snapshot["summary"]["blockers"]))
        self.assertIn("Dead-letter jobs", " ".join(snapshot["summary"]["blockers"]))

    def test_deployment_readiness_snapshot_rolls_up_artifacts_runbooks_and_backup_manifest(self) -> None:
        from backend.services import deployment_service

        fake_settings = SimpleNamespace(
            backup_restore_warning_days=30,
            environment="production",
            reload=False,
            allow_demo_auth=False,
            auth_enabled=True,
            auth_provider="auth0",
            database_url="postgresql://pilot-db.example.com/stocksignals",
            auth_session_secret="pilot-session-secret",
            auth_state_secret="pilot-state-secret",
            api_token_salt="pilot-token-salt",
            market_data_provider="alpaca",
            alpaca_api_key_id="alpaca-key",
            alpaca_api_secret_key="alpaca-secret",
            stripe_publishable_key="pk_live_123",
            stripe_secret_key="sk_live_123",
            stripe_webhook_secret="whsec_123",
        )
        with _workspace_tempdir() as temp_dir:
            root = Path(temp_dir)
            (root / "backend").mkdir(parents=True, exist_ok=True)
            (root / "frontend").mkdir(parents=True, exist_ok=True)
            (root / "docs" / "runbooks").mkdir(parents=True, exist_ok=True)
            (root / "runtime-logs").mkdir(parents=True, exist_ok=True)

            for relative_path in [
                "docker-compose.yml",
                "backend/Dockerfile",
                "frontend/Dockerfile",
                ".env.example",
                "Makefile",
                "docs/runbooks/deployment.md",
                "docs/runbooks/backup_restore.md",
                "docs/runbooks/incident_response.md",
                "docs/runbooks/rollback.md",
                "docs/runbooks/slow_app.md",
                "docs/runbooks/stale_feed.md",
                "docs/runbooks/backlog_recovery.md",
                "docs/runbooks/own_account_intraday_implementation_checklist.md",
            ]:
                path = root / relative_path
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("ready", encoding="utf-8")

            (root / "runtime-logs" / "backup-status.json").write_text(
                json.dumps(
                    {
                        "status": "attention",
                        "provider": "local-volume-snapshot",
                        "schedule": "nightly",
                        "last_success_at": "2026-04-15T03:00:00+00:00",
                        "last_attempt_at": "2026-04-15T03:00:00+00:00",
                        "restore_tested_at": None,
                        "retention_days": 14,
                        "location": "backend-storage volume",
                    }
                ),
                encoding="utf-8",
            )

            with patch.object(deployment_service, "settings", fake_settings):
                snapshot = deployment_service.get_deployment_readiness_snapshot(project_root=root)

        self.assertEqual(snapshot["deployment"]["ready_count"], snapshot["deployment"]["count"])
        self.assertEqual(snapshot["runbooks"]["ready_count"], snapshot["runbooks"]["count"])
        self.assertEqual(snapshot["runbooks"]["count"], 8)
        self.assertTrue(snapshot["backups"]["configured"])
        self.assertEqual(snapshot["backups"]["provider"], "local-volume-snapshot")
        self.assertTrue(snapshot["backups"]["needs_attention"])
        self.assertIn("Restore drill has not been recorded yet.", snapshot["summary"]["blockers"])

    def test_deployment_readiness_snapshot_flags_invalid_manifest_and_stale_restore_drill(self) -> None:
        from backend.services import deployment_service

        fake_settings = SimpleNamespace(
            backup_restore_warning_days=30,
            environment="production",
            reload=False,
            allow_demo_auth=False,
            auth_enabled=True,
            auth_provider="auth0",
            database_url="postgresql://pilot-db.example.com/stocksignals",
            auth_session_secret="pilot-session-secret",
            auth_state_secret="pilot-state-secret",
            api_token_salt="pilot-token-salt",
            market_data_provider="alpaca",
            alpaca_api_key_id="alpaca-key",
            alpaca_api_secret_key="alpaca-secret",
            stripe_publishable_key="pk_live_123",
            stripe_secret_key="sk_live_123",
            stripe_webhook_secret="whsec_123",
        )
        with _workspace_tempdir() as temp_dir:
            root = Path(temp_dir)
            (root / "backend").mkdir(parents=True, exist_ok=True)
            (root / "frontend").mkdir(parents=True, exist_ok=True)
            (root / "docs" / "runbooks").mkdir(parents=True, exist_ok=True)
            (root / "runtime-logs").mkdir(parents=True, exist_ok=True)

            for relative_path in [
                "docker-compose.yml",
                "backend/Dockerfile",
                "frontend/Dockerfile",
                ".env.example",
                "Makefile",
                "docs/runbooks/deployment.md",
                "docs/runbooks/backup_restore.md",
                "docs/runbooks/incident_response.md",
                "docs/runbooks/rollback.md",
                "docs/runbooks/slow_app.md",
                "docs/runbooks/stale_feed.md",
                "docs/runbooks/backlog_recovery.md",
                "docs/runbooks/own_account_intraday_implementation_checklist.md",
            ]:
                path = root / relative_path
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("ready", encoding="utf-8")

            (root / "runtime-logs" / "backup-status.json").write_text(
                json.dumps(
                    {
                        "status": "ready",
                        "provider": "local-volume-snapshot",
                        "schedule": "nightly",
                        "last_success_at": "2026-04-15T03:00:00+00:00",
                        "last_attempt_at": "2026-04-15T03:15:00+00:00",
                        "restore_tested_at": "2026-02-01T03:00:00+00:00",
                        "retention_days": 0,
                        "location": "backend-storage volume",
                    }
                ),
                encoding="utf-8",
            )

            with (
                patch.object(deployment_service, "settings", fake_settings),
                patch.object(deployment_service, "_utc_now", return_value=datetime(2026, 4, 17, 12, 0, tzinfo=timezone.utc)),
            ):
                snapshot = deployment_service.get_deployment_readiness_snapshot(project_root=root)

        self.assertEqual(snapshot["summary"]["status"], "attention")
        self.assertIn("Backup manifest is invalid and should be corrected before pilot launch.", snapshot["summary"]["blockers"])
        self.assertTrue(snapshot["summary"]["warnings"])
        self.assertIn("Restore drill is 75.4 days old", snapshot["summary"]["warnings"][0])
        self.assertFalse(snapshot["backups"]["validation"]["valid"])
        self.assertEqual(snapshot["backups"]["validation"]["issue_count"], 1)
        self.assertIn("Retention days must be a positive integer", snapshot["backups"]["validation"]["issues"][0])
        self.assertEqual(snapshot["backups"]["restore_warning_days"], 30)
        self.assertEqual(snapshot["backups"]["restore_age_days"], 75.4)

    def test_deployment_readiness_snapshot_validates_required_production_settings(self) -> None:
        from backend.services import deployment_service

        fake_settings = SimpleNamespace(
            backup_restore_warning_days=30,
            environment="development",
            reload=True,
            allow_demo_auth=True,
            auth_enabled=False,
            auth_provider="local-demo",
            database_url="sqlite:///backend/storage/app.db",
            auth_session_secret="stocksignals-local-session-secret",
            auth_state_secret="stocksignals-local-auth-state-secret",
            api_token_salt="stocksignals-local-token-salt",
            market_data_provider="alpaca",
            alpaca_api_key_id="",
            alpaca_api_secret_key="",
            stripe_publishable_key="",
            stripe_secret_key="",
            stripe_webhook_secret="",
        )

        with patch.object(deployment_service, "settings", fake_settings):
            snapshot = deployment_service.get_deployment_readiness_snapshot(project_root=Path.cwd())

        self.assertEqual(snapshot["environment"]["summary"]["status"], "blocked")
        self.assertIn("APP_ENV is still set to a development-style environment.", snapshot["environment"]["summary"]["blockers"])
        self.assertIn("API reload is still enabled.", snapshot["environment"]["summary"]["blockers"])
        self.assertIn("DATABASE_URL still points at a local SQLite database.", snapshot["environment"]["summary"]["blockers"])
        self.assertIn("Stripe billing keys are not configured yet.", snapshot["environment"]["summary"]["warnings"])
        self.assertTrue(any(item["key"] == "auth_provider" and item["status"] == "blocked" for item in snapshot["environment"]["checks"]))

    def test_deployment_readiness_snapshot_operator_local_profile_downgrades_demo_auth_and_sqlite(self) -> None:
        from backend.services import deployment_service

        fake_settings = SimpleNamespace(
            backup_restore_warning_days=30,
            environment="staging",
            enterprise_runtime_profile="operator-local",
            reload=False,
            allow_demo_auth=True,
            auth_enabled=False,
            auth_provider="local-demo",
            database_url="sqlite:///backend/storage/app.db",
            auth_session_secret="rotated-session-secret",
            auth_state_secret="rotated-state-secret",
            api_token_salt="rotated-token-salt",
            market_data_provider="alpaca",
            alpaca_api_key_id="configured",
            alpaca_api_secret_key="configured",
            stripe_publishable_key="",
            stripe_secret_key="",
            stripe_webhook_secret="",
        )

        with patch.object(deployment_service, "settings", fake_settings):
            snapshot = deployment_service.get_deployment_readiness_snapshot(project_root=Path.cwd())

        self.assertEqual(snapshot["environment"]["summary"]["status"], "warning")
        self.assertIn("Demo auth remains enabled for operator-local mode.", snapshot["environment"]["summary"]["warnings"])
        self.assertIn("Local SQLite is active for operator-local mode; keep backup and restore drills current.", snapshot["environment"]["summary"]["warnings"])
        self.assertTrue(any(item["key"] == "auth_provider" and item["status"] == "warning" for item in snapshot["environment"]["checks"]))
        self.assertTrue(any(item["key"] == "database_url" and item["status"] == "warning" for item in snapshot["environment"]["checks"]))

    def test_job_metrics_snapshot_flags_stuck_running_jobs(self) -> None:
        from datetime import timedelta

        from backend.core.database import Base
        from backend.models.saas import AsyncJob
        from backend.services import job_queue_service

        engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
        Session = sessionmaker(bind=engine, expire_on_commit=False, future=True)
        Base.metadata.create_all(bind=engine)

        with Session() as session:
            old_started_at = datetime.now(timezone.utc) - timedelta(minutes=20)
            session.add(
                AsyncJob(
                    job_type="partner_webhook_delivery",
                    status="running",
                    payload_json={},
                    result_json={},
                    attempt_count=1,
                    max_attempts=4,
                    available_at=old_started_at,
                    started_at=old_started_at,
                )
            )
            session.commit()

            snapshot = job_queue_service.get_job_metrics_snapshot(session)

        self.assertEqual(snapshot["summary"]["running"], 1)
        self.assertEqual(snapshot["summary"]["stuck_running_count"], 1)
        self.assertEqual(snapshot["summary"]["running_stale_after_minutes"], 10)
        self.assertTrue(snapshot["summary"]["oldest_running_at"])
        self.assertEqual(snapshot["stuck_running"][0]["job_type"], "partner_webhook_delivery")

    def test_recover_stale_running_jobs_requeues_incomplete_attempts(self) -> None:
        from datetime import timedelta

        from backend.core.database import Base
        from backend.models.saas import AsyncJob
        from backend.services import job_queue_service

        engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
        Session = sessionmaker(bind=engine, expire_on_commit=False, future=True)
        Base.metadata.create_all(bind=engine)

        with Session() as session:
            old_started_at = datetime.now(timezone.utc) - timedelta(minutes=20)
            session.add(
                AsyncJob(
                    job_type="partner_webhook_delivery",
                    status="running",
                    payload_json={},
                    result_json={},
                    attempt_count=1,
                    max_attempts=4,
                    available_at=old_started_at,
                    started_at=old_started_at,
                )
            )
            session.commit()

            summary = job_queue_service.recover_stale_running_jobs(session)
            recovered = session.query(AsyncJob).one()

        self.assertEqual(summary["recovered"], 1)
        self.assertEqual(summary["dead_lettered"], 0)
        self.assertEqual(recovered.status, "retrying")
        self.assertIsNone(recovered.started_at)
        self.assertTrue(recovered.finished_at)
        self.assertIn("Recovered stale running job", recovered.error_message or "")

    def test_open_trade_records_order_lifecycle_events(self) -> None:
        from backend.core.database import Base
        from backend.models.saas import OrderEventRecord
        from backend.schemas import OpenTradeRequest
        from backend.services import tenant_service, trade_service

        engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
        Session = sessionmaker(bind=engine, expire_on_commit=False, future=True)
        Base.metadata.create_all(bind=engine)

        report = {
            "ticker": "SPY",
            "interval": "5m",
            "verdict": "BULLISH",
            "alignment_label": "Aligned",
            "conviction_label": "HIGH CONVICTION",
            "setup_score": 84.0,
            "setup_grade": "A setup",
            "trade_decision": "VALID TRADE",
            "reject_reason": "",
            "event_risk": False,
            "event_label": "",
            "event_reason": "",
            "next_event_name": "",
            "next_event_date": "",
            "option_plan": {
                "recommended_contract": {
                    "contract_symbol": "SPY260417C00501000",
                    "mid": 4.2,
                    "bid": 4.1,
                    "ask": 4.3,
                    "spread_pct": 0.048,
                    "volume": 250,
                    "open_interest": 1200,
                    "quote_timestamp": "2026-04-22T14:00:00+00:00",
                },
                "expected_underlying_target": 510.0,
                "invalidation_price": 496.0,
                "take_profit_1": 0.2,
                "take_profit_2": 0.35,
            },
        }
        position = {
            "suggested_contracts": 2,
            "total_position_cost": 840.0,
            "max_risk_dollars": 125.0,
        }
        fake_settings = SimpleNamespace(
            demo_tenant_slug="alpha-desk",
            demo_tenant_name="Alpha Desk",
            demo_tenant_plan="pro",
        )

        with (
            Session() as db,
            patch.object(tenant_service, "settings", fake_settings),
            patch.object(trade_service, "analyze_market", return_value={"report": report, "live_price": 501.2}),
            patch.object(trade_service, "_validate_option_execution_request", return_value={}),
            patch.object(trade_service.sdm, "calculate_position_sizing", return_value=position),
            patch.object(trade_service.sdm, "read_open_trades", return_value=pd.DataFrame()),
            patch.object(trade_service.sdm, "read_pending_orders", return_value=pd.DataFrame()),
            patch.object(trade_service.sdm, "read_closed_trades", return_value=pd.DataFrame()),
            patch.object(trade_service.sdm, "append_pending_order"),
        ):
            identity = tenant_service.ensure_demo_tenant_for_user(
                db,
                auth_subject="demo-trader",
                email="demo@example.test",
                name="Demo Trader",
                provider="local-demo",
                requested_tenant_slug="alpha-desk",
            )
            current_user = SimpleNamespace(
                tenant_id=identity["active_tenant"].id,
                tenant_slug="alpha-desk",
                tenant_name="Alpha Desk",
                tenant_status="active",
                tenant_plan="pro",
                auth_subject="demo-trader",
                user_id="demo-trader",
            )

            payload = trade_service.open_trade_from_request(
                OpenTradeRequest(
                    ticker="SPY",
                    interval="5m",
                    horizon=5,
                    live_price=501.2,
                    account_size=10000,
                    risk_percent=1.0,
                    order_type="limit",
                    time_in_force="day_ext",
                    limit_price=501.0,
                    extended_hours=True,
                ),
                db=db,
                current_user=current_user,
            )
            stored_events = db.execute(select(OrderEventRecord).order_by(OrderEventRecord.created_at.asc())).scalars().all()

        self.assertTrue(payload["opened"])
        self.assertFalse(payload["position_opened"])
        self.assertIsNotNone(payload["pending_order"])
        self.assertEqual(payload["pending_order"]["route_family"], "legacy")
        self.assertEqual(payload["pending_order"]["validation_sample_bucket"], "legacy")
        self.assertEqual(payload["pending_order"]["directional_exposure"], "bullish")
        self.assertEqual(payload["order_events"]["count"], 2)
        self.assertEqual(payload["latest_order_event"]["status"], "working")
        self.assertEqual([row.event_key for row in stored_events], ["order.submitted", "order.accepted"])
        self.assertEqual(stored_events[-1].route_state, "accepted")

    def test_open_trade_creates_client_trade_intent_without_personal_order_submission(self) -> None:
        from backend.core.database import Base
        from backend.models.saas import BrokerageLinkedAccount, OrderEventRecord, TradeApprovalIntent, User
        from backend.schemas import OpenTradeRequest
        from backend.services import tenant_service, trade_service

        engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
        Session = sessionmaker(bind=engine, expire_on_commit=False, future=True)
        Base.metadata.create_all(bind=engine)

        fake_settings = SimpleNamespace(
            demo_tenant_slug="alpha-desk",
            demo_tenant_name="Alpha Desk",
            demo_tenant_plan="pro",
        )
        report = {
            "ticker": "SPY",
            "interval": "5m",
            "trade_decision": "VALID TRADE",
            "reject_reason": "",
            "event_risk": False,
            "option_plan": {
                "recommended_contract": None,
                "expected_underlying_target": 510.0,
                "invalidation_price": 496.0,
                "take_profit_1": 0.2,
                "take_profit_2": 0.35,
            },
        }
        position = {
            "suggested_contracts": 2,
            "total_position_cost": 840.0,
            "max_risk_dollars": 125.0,
        }

        with (
            Session() as db,
            patch.object(tenant_service, "settings", fake_settings),
            patch.object(trade_service, "analyze_market", return_value={"report": report, "live_price": 501.2}),
            patch.object(trade_service, "_validate_directional_entry_request"),
            patch.object(trade_service, "_validate_option_execution_request", return_value={}),
            patch.object(trade_service, "_validate_capital_preservation_request", return_value={}),
            patch.object(trade_service, "_build_order_ticket_payload", return_value={"contract_symbol": "SPY240621C00510000"}),
            patch.object(trade_service, "_build_equity_position_preview", return_value=position),
            patch.object(trade_service.sdm, "calculate_position_sizing", return_value=position),
            patch.object(trade_service.sdm, "read_open_trades", return_value=pd.DataFrame()),
            patch.object(trade_service.sdm, "read_pending_orders", return_value=pd.DataFrame()),
            patch.object(trade_service.sdm, "read_closed_trades", return_value=pd.DataFrame()),
            patch.object(trade_service, "get_execution_adapter_for") as get_execution_adapter_for_mock,
        ):
            identity = tenant_service.ensure_demo_tenant_for_user(
                db,
                auth_subject="demo-trader",
                email="demo@example.test",
                name="Demo Trader",
                provider="local-demo",
                requested_tenant_slug="alpha-desk",
            )
            actor = db.execute(select(User).where(User.auth_subject == "demo-trader")).scalar_one()
            linked_account = BrokerageLinkedAccount(
                tenant=identity["active_tenant"],
                owner_user=actor,
                provider="alpaca",
                label="Client Paper",
                account_environment="paper",
                connection_status="connected",
                token_health="healthy",
                approval_policy="approval_required",
                oauth_access_token="oauth-token",
            )
            db.add(linked_account)
            db.commit()
            current_user = SimpleNamespace(
                tenant_id=identity["active_tenant"].id,
                tenant_slug="alpha-desk",
                tenant_name="Alpha Desk",
                tenant_status="active",
                tenant_plan="pro",
                auth_subject="demo-trader",
                user_id="demo-trader",
            )

            payload = trade_service.open_trade_from_request(
                OpenTradeRequest(
                    ticker="SPY",
                    interval="5m",
                    horizon=5,
                    live_price=501.2,
                    account_size=10000,
                    risk_percent=1.0,
                    order_type="limit",
                    time_in_force="day",
                    limit_price=5.1,
                    option_right="call",
                    contract_symbol="SPY240621C00510000",
                    contract_expiration="2024-06-21",
                    contract_strike=510.0,
                    account_target_type="linked_client",
                    linked_account_id=linked_account.id,
                ),
                db=db,
                current_user=current_user,
            )
            stored_intents = db.execute(select(TradeApprovalIntent)).scalars().all()
            stored_events = db.execute(select(OrderEventRecord)).scalars().all()

        self.assertFalse(payload["opened"])
        self.assertTrue(payload["intent_created"])
        self.assertEqual(payload["execution"]["account_target_type"], "linked_client")
        self.assertEqual(payload["trade_intent"]["status"], "pending_approval")
        self.assertTrue(payload["trade_intent"]["broker_case"]["case_id"].startswith("CTC-"))
        self.assertIn(payload["trade_intent"]["broker_case"]["risk_band"], {"low", "moderate", "high", "critical"})
        self.assertTrue(payload["trade_intent"]["broker_case"]["verification_checklist"])
        self.assertEqual(
            payload["trade_intent"]["strategy_release_snapshot"]["schema_version"],
            "broker_strategy_release_v1",
        )
        self.assertEqual(payload["trade_intent"]["trust_packet_summary"]["packet_version"], "1.0")
        self.assertEqual(len(stored_intents), 1)
        self.assertEqual(stored_intents[0].linked_account_id, linked_account.id)
        self.assertIn("broker_case", stored_intents[0].metadata_json)
        self.assertIn("strategy_release_snapshot", stored_intents[0].metadata_json)
        self.assertEqual(stored_events, [])
        get_execution_adapter_for_mock.assert_not_called()

    def test_approve_trade_intent_uses_linked_account_oauth_client(self) -> None:
        from backend.core.database import Base
        from backend.models.saas import AuditEvent, BrokerageLinkedAccount, TradeApprovalIntent, User
        from backend.schemas import OpenTradeRequest
        from backend.services import tenant_service, trade_service

        engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
        Session = sessionmaker(bind=engine, expire_on_commit=False, future=True)
        Base.metadata.create_all(bind=engine)

        fake_settings = SimpleNamespace(
            demo_tenant_slug="alpha-desk",
            demo_tenant_name="Alpha Desk",
            demo_tenant_plan="pro",
        )
        with (
            Session() as db,
            patch.object(tenant_service, "settings", fake_settings),
            patch.object(trade_service, "get_execution_adapter_for") as get_execution_adapter_for_mock,
        ):
            identity = tenant_service.ensure_demo_tenant_for_user(
                db,
                auth_subject="demo-trader",
                email="demo@example.test",
                name="Demo Trader",
                provider="local-demo",
                requested_tenant_slug="alpha-desk",
            )
            actor = db.execute(select(User).where(User.auth_subject == "demo-trader")).scalar_one()
            linked_account = BrokerageLinkedAccount(
                tenant=identity["active_tenant"],
                owner_user=actor,
                provider="alpaca",
                label="Client Paper",
                account_environment="paper",
                connection_status="connected",
                token_health="healthy",
                approval_policy="approval_required",
                oauth_access_token="oauth-token",
            )
            intent = TradeApprovalIntent(
                tenant=identity["active_tenant"],
                requester_user=actor,
                linked_account=linked_account,
                provider="alpaca",
                execution_lane="linked_client",
                status="pending_approval",
                ticker="SPY",
                instrument_type="equity",
                account_environment="paper",
                trade_id="trade-client-1",
                order_id="order-client-1",
                route_correlation_id="corr-client-1",
                request_payload_json=OpenTradeRequest(
                    ticker="SPY",
                    interval="5m",
                    horizon=5,
                    live_price=500.5,
                    account_size=10000,
                    risk_percent=0.5,
                    instrument_type="equity",
                    order_type="limit",
                    time_in_force="day",
                    limit_price=500.5,
                    account_target_type="linked_client",
                    linked_account_id="placeholder",
                ).model_dump(),
                analysis_json={"ticker": "SPY"},
                position_json={"suggested_contracts": 5},
                order_ticket_json={"order_id": "order-client-1"},
                broker_submit_payload_json={
                    "symbol": "SPY",
                    "qty": "5",
                    "side": "buy",
                    "type": "limit",
                    "time_in_force": "day",
                    "limit_price": "500.5",
                },
            )
            db.add_all([linked_account, intent])
            db.commit()
            current_user = SimpleNamespace(
                tenant_id=identity["active_tenant"].id,
                tenant_slug="alpha-desk",
                tenant_name="Alpha Desk",
                tenant_status="active",
                tenant_plan="pro",
                auth_subject="demo-trader",
                user_id="demo-trader",
            )
            oauth_client = MagicMock()
            oauth_client.submit_order.return_value = {"id": "broker-order-1", "status": "accepted"}
            with patch.object(trade_service, "build_linked_account_execution_client", return_value=oauth_client):
                payload = trade_service.approve_trade_intent(
                    intent.id,
                    note="approved",
                    db=db,
                    current_user=current_user,
                )
            db.refresh(intent)
            audit_events = db.execute(select(AuditEvent).order_by(AuditEvent.created_at.asc())).scalars().all()

        self.assertTrue(payload["approved"])
        self.assertTrue(payload["submitted"])
        self.assertEqual(payload["trade_intent"]["status"], "submitted")
        self.assertEqual(intent.broker_order_id, "broker-order-1")
        self.assertEqual(intent.broker_status, "accepted")
        oauth_client.submit_order.assert_called_once()
        get_execution_adapter_for_mock.assert_not_called()
        self.assertIn("client_trade_intent.approved", [row.event_type for row in audit_events])
        self.assertIn("client_trade_intent.submitted", [row.event_type for row in audit_events])

    def test_conditional_trade_intent_records_conditions_and_trust_packet(self) -> None:
        from backend.core.database import Base
        from backend.models.saas import AuditEvent, BrokerageLinkedAccount, TradeApprovalIntent, User
        from backend.schemas import OpenTradeRequest
        from backend.services import tenant_service, trade_service

        engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
        Session = sessionmaker(bind=engine, expire_on_commit=False, future=True)
        Base.metadata.create_all(bind=engine)

        fake_settings = SimpleNamespace(
            demo_tenant_slug="alpha-desk",
            demo_tenant_name="Alpha Desk",
            demo_tenant_plan="pro",
        )
        with (
            Session() as db,
            patch.object(tenant_service, "settings", fake_settings),
        ):
            identity = tenant_service.ensure_demo_tenant_for_user(
                db,
                auth_subject="demo-trader",
                email="demo@example.test",
                name="Demo Trader",
                provider="local-demo",
                requested_tenant_slug="alpha-desk",
            )
            actor = db.execute(select(User).where(User.auth_subject == "demo-trader")).scalar_one()
            linked_account = BrokerageLinkedAccount(
                tenant=identity["active_tenant"],
                owner_user=actor,
                provider="alpaca",
                label="Client Paper",
                account_environment="paper",
                connection_status="connected",
                token_health="healthy",
                approval_policy="approval_required",
                oauth_access_token="oauth-token",
            )
            intent = TradeApprovalIntent(
                tenant=identity["active_tenant"],
                requester_user=actor,
                linked_account=linked_account,
                provider="alpaca",
                execution_lane="linked_client",
                status="pending_approval",
                ticker="SPY",
                instrument_type="equity",
                account_environment="paper",
                trade_id="trade-client-conditional",
                order_id="order-client-conditional",
                route_correlation_id="corr-client-conditional",
                request_payload_json=OpenTradeRequest(
                    ticker="SPY",
                    interval="5m",
                    horizon=5,
                    live_price=500.5,
                    account_size=10000,
                    risk_percent=0.5,
                    instrument_type="equity",
                    order_type="limit",
                    time_in_force="day",
                    limit_price=500.5,
                    account_target_type="linked_client",
                    linked_account_id="placeholder",
                ).model_dump(),
                analysis_json={"ticker": "SPY", "trade_decision": "VALID TRADE"},
                position_json={"suggested_contracts": 5, "total_position_cost": 2502.5},
                order_ticket_json={"order_id": "order-client-conditional"},
                broker_submit_payload_json={
                    "symbol": "SPY",
                    "qty": "5",
                    "side": "buy",
                    "type": "limit",
                    "time_in_force": "day",
                    "limit_price": "500.5",
                },
            )
            db.add_all([linked_account, intent])
            db.commit()
            current_user = SimpleNamespace(
                tenant_id=identity["active_tenant"].id,
                tenant_slug="alpha-desk",
                tenant_name="Alpha Desk",
                tenant_status="active",
                tenant_plan="pro",
                auth_subject="demo-trader",
                user_id="demo-trader",
            )

            payload = trade_service.conditionally_approve_trade_intent(
                intent.id,
                note="Risk reviewed for the client account before submission.",
                conditions=["Refresh quote before submit", "Confirm client consent"],
                db=db,
                current_user=current_user,
            )
            packet = trade_service.get_trade_intent_trust_packet(intent.id, db=db, current_user=current_user)
            db.refresh(intent)
            audit_events = db.execute(select(AuditEvent).order_by(AuditEvent.created_at.asc())).scalars().all()

        self.assertTrue(payload["conditionally_approved"])
        self.assertEqual(payload["trade_intent"]["status"], "conditionally_approved")
        self.assertEqual(intent.status, "conditionally_approved")
        self.assertEqual(
            payload["trade_intent"]["broker_case"]["latest_decision"]["conditions"],
            ["Refresh quote before submit", "Confirm client consent"],
        )
        self.assertEqual(packet["packet_type"], "broker_trust_packet")
        self.assertEqual(packet["broker_review_case"]["case_id"], payload["trade_intent"]["broker_case"]["case_id"])
        self.assertIn("decision_review", packet)
        self.assertIn("evidence_register", packet)
        self.assertTrue(packet["packet_fingerprint"])
        self.assertEqual(packet["decision_history"]["latest_decision"]["action"], "conditionally_approved")
        self.assertIn("client_trade_intent.conditionally_approved", [row.event_type for row in audit_events])
        self.assertIn("client_trade_intent.conditionally_approved", [row["event_type"] for row in packet["audit_timeline"]])

    def test_trade_decision_review_blocks_ready_until_required_fields(self) -> None:
        from backend.core.database import Base
        from backend.models.saas import AuditEvent, BrokerageLinkedAccount, TradeApprovalIntent, User
        from backend.schemas import OpenTradeRequest, TradeDecisionReviewRequest
        from backend.services import tenant_service, trade_workflow_service

        engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
        Session = sessionmaker(bind=engine, expire_on_commit=False, future=True)
        Base.metadata.create_all(bind=engine)

        fake_settings = SimpleNamespace(
            demo_tenant_slug="alpha-desk",
            demo_tenant_name="Alpha Desk",
            demo_tenant_plan="pro",
        )
        with (
            Session() as db,
            patch.object(tenant_service, "settings", fake_settings),
        ):
            identity = tenant_service.ensure_demo_tenant_for_user(
                db,
                auth_subject="demo-trader",
                email="demo@example.test",
                name="Demo Trader",
                provider="local-demo",
                requested_tenant_slug="alpha-desk",
            )
            actor = db.execute(select(User).where(User.auth_subject == "demo-trader")).scalar_one()
            linked_account = BrokerageLinkedAccount(
                tenant=identity["active_tenant"],
                owner_user=actor,
                provider="alpaca",
                label="Client Paper",
                account_environment="paper",
                connection_status="connected",
                token_health="healthy",
                approval_policy="approval_required",
                oauth_access_token="oauth-token",
            )
            intent = TradeApprovalIntent(
                tenant=identity["active_tenant"],
                requester_user=actor,
                linked_account=linked_account,
                provider="alpaca",
                execution_lane="linked_client",
                status="pending_approval",
                ticker="SPY",
                instrument_type="equity",
                account_environment="paper",
                trade_id="trade-client-review",
                order_id="order-client-review",
                request_payload_json=OpenTradeRequest(
                    ticker="SPY",
                    interval="5m",
                    horizon=5,
                    live_price=500.5,
                    account_size=10000,
                    risk_percent=0.5,
                    instrument_type="equity",
                    order_type="limit",
                    time_in_force="day",
                    limit_price=500.5,
                    account_target_type="linked_client",
                    linked_account_id="placeholder",
                ).model_dump(),
                analysis_json={
                    "ticker": "SPY",
                    "trade_decision": "VALID TRADE",
                    "event_risk": False,
                    "option_plan": {
                        "expected_underlying_target": 508.0,
                        "invalidation_price": 496.0,
                        "stop_loss": 496.0,
                    },
                },
                position_json={"suggested_contracts": 5, "target_price": 508.0, "stop_loss": 496.0},
                order_ticket_json={"order_id": "order-client-review"},
                metadata_json={
                    "liquidity_execution": {"status": "pass", "route": "paper_limit"},
                    "route_eligibility": {"eligible": True},
                    "strategy_release_snapshot": {"release_id": "desk:v1", "status": "released"},
                },
            )
            db.add_all([linked_account, intent])
            db.commit()
            current_user = SimpleNamespace(
                tenant_id=identity["active_tenant"].id,
                tenant_slug="alpha-desk",
                tenant_name="Alpha Desk",
                tenant_status="active",
                tenant_plan="pro",
                auth_subject="demo-trader",
                user_id="demo-trader",
            )

            with self.assertRaises(ValidationServiceError):
                trade_workflow_service.update_trade_intent_decision_review(
                    intent.id,
                    TradeDecisionReviewRequest(mark_decision_ready=True),
                    db=db,
                    current_user=current_user,
                )

            payload = trade_workflow_service.update_trade_intent_decision_review(
                intent.id,
                TradeDecisionReviewRequest(
                    standard_path="Standard paper recommendation review path.",
                    requested_deviation="No deviation requested.",
                    thesis_rationale="SPY holds the planned entry zone with defined stop, target, and limited account risk.",
                    accepted_risk=True,
                    accepted_risk_owner="Demo Trader",
                    accepted_risk_note="Owner accepts the paper route risk and invalidation level.",
                    challenge_raised=False,
                    unresolved_conditions=[],
                    mark_decision_ready=True,
                ),
                db=db,
                current_user=current_user,
            )
            audit_events = db.execute(select(AuditEvent).order_by(AuditEvent.created_at.asc())).scalars().all()

        review = payload["decision_review"]
        self.assertTrue(review["marked_decision_ready"])
        self.assertTrue(review["readiness"]["ready_for_final_decision"])
        self.assertEqual(review["readiness"]["blockers"], [])
        self.assertIn("trade_decision_review.updated", [row.event_type for row in audit_events])

    def test_saved_trade_scenario_contrast_groups_start_with_opposite_outcomes(self) -> None:
        from backend.core.database import Base
        from backend.models.saas import BrokerageLinkedAccount, TradeApprovalIntent, User
        from backend.schemas import OpenTradeRequest, TradeScenarioSaveRequest
        from backend.services import tenant_service, trade_workflow_service

        engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
        Session = sessionmaker(bind=engine, expire_on_commit=False, future=True)
        Base.metadata.create_all(bind=engine)

        fake_settings = SimpleNamespace(
            demo_tenant_slug="alpha-desk",
            demo_tenant_name="Alpha Desk",
            demo_tenant_plan="pro",
        )
        with _workspace_tempdir() as temp_dir:
            workflow_path = Path(temp_dir) / "trade_workflow.json"
            with (
                Session() as db,
                patch.object(tenant_service, "settings", fake_settings),
                patch.object(trade_workflow_service, "_WORKFLOW_STORE_FILE", workflow_path),
            ):
                identity = tenant_service.ensure_demo_tenant_for_user(
                    db,
                    auth_subject="demo-trader",
                    email="demo@example.test",
                    name="Demo Trader",
                    provider="local-demo",
                    requested_tenant_slug="alpha-desk",
                )
                actor = db.execute(select(User).where(User.auth_subject == "demo-trader")).scalar_one()
                linked_account = BrokerageLinkedAccount(
                    tenant=identity["active_tenant"],
                    owner_user=actor,
                    provider="alpaca",
                    label="Client Paper",
                    account_environment="paper",
                    connection_status="connected",
                    token_health="healthy",
                    approval_policy="approval_required",
                    oauth_access_token="oauth-token",
                )
                intent = TradeApprovalIntent(
                    tenant=identity["active_tenant"],
                    requester_user=actor,
                    linked_account=linked_account,
                    provider="alpaca",
                    execution_lane="linked_client",
                    status="submitted",
                    ticker="SPY",
                    instrument_type="equity",
                    account_environment="paper",
                    trade_id="trade-client-scenario",
                    order_id="order-client-scenario",
                    request_payload_json=OpenTradeRequest(
                        ticker="SPY",
                        interval="5m",
                        horizon=5,
                        live_price=500.5,
                        account_size=10000,
                        risk_percent=0.5,
                        instrument_type="equity",
                        order_type="limit",
                        time_in_force="day",
                        limit_price=500.5,
                        account_target_type="linked_client",
                        linked_account_id="placeholder",
                        thesis_direction="breakout",
                    ).model_dump(),
                    analysis_json={"ticker": "SPY", "trade_decision": "VALID TRADE"},
                    position_json={"suggested_contracts": 5},
                    order_ticket_json={"order_id": "order-client-scenario"},
                    metadata_json={"liquidity_execution": {"status": "pass"}},
                )
                db.add_all([linked_account, intent])
                db.commit()
                current_user = SimpleNamespace(
                    tenant_id=identity["active_tenant"].id,
                    tenant_slug="alpha-desk",
                    tenant_name="Alpha Desk",
                    tenant_status="active",
                    tenant_plan="pro",
                    auth_subject="demo-trader",
                    user_id="demo-trader",
                )

                trade_workflow_service.save_trade_scenario_from_intent(
                    intent.id,
                    TradeScenarioSaveRequest(name="SPY win", outcome="win", setup_label="breakout", market_regime="trend"),
                    db=db,
                    current_user=current_user,
                )
                trade_workflow_service.save_trade_scenario_from_intent(
                    intent.id,
                    TradeScenarioSaveRequest(name="SPY loss", outcome="loss", setup_label="breakout", market_regime="chop"),
                    db=db,
                    current_user=current_user,
                )
                scenarios = trade_workflow_service.list_trade_scenarios(db=db, current_user=current_user)

        group = scenarios["comparison_groups"][0]
        first_states = [item["decision_state"] for item in group["items"][:2]]
        self.assertTrue(group["contrast_ready"])
        self.assertEqual(set(first_states), {"win", "loss"})

    def test_control_change_request_creates_pending_case_and_audit(self) -> None:
        from backend.core.database import Base
        from backend.models.saas import AuditEvent, User
        from backend.schemas import ControlChangeRequest
        from backend.services import tenant_service, trade_workflow_service

        engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
        Session = sessionmaker(bind=engine, expire_on_commit=False, future=True)
        Base.metadata.create_all(bind=engine)

        fake_settings = SimpleNamespace(
            demo_tenant_slug="alpha-desk",
            demo_tenant_name="Alpha Desk",
            demo_tenant_plan="pro",
        )
        with _workspace_tempdir() as temp_dir:
            workflow_path = Path(temp_dir) / "trade_workflow.json"
            with (
                Session() as db,
                patch.object(tenant_service, "settings", fake_settings),
                patch.object(trade_workflow_service, "_WORKFLOW_STORE_FILE", workflow_path),
            ):
                identity = tenant_service.ensure_demo_tenant_for_user(
                    db,
                    auth_subject="demo-trader",
                    email="demo@example.test",
                    name="Demo Trader",
                    provider="local-demo",
                    requested_tenant_slug="alpha-desk",
                )
                db.execute(select(User).where(User.auth_subject == "demo-trader")).scalar_one()
                current_user = SimpleNamespace(
                    tenant_id=identity["active_tenant"].id,
                    tenant_slug="alpha-desk",
                    tenant_name="Alpha Desk",
                    tenant_status="active",
                    tenant_plan="pro",
                    auth_subject="demo-trader",
                    user_id="demo-trader",
                )
                payload = trade_workflow_service.request_control_change_case(
                    ControlChangeRequest(
                        control_type="live_mode_activation",
                        summary="Enable live mode for the paper-validated account.",
                        applies_to="Client Paper",
                        current_value="paper",
                        requested_value="live",
                        rationale="Paper route has been reviewed and needs a controlled live pilot.",
                    ),
                    db=db,
                    current_user=current_user,
                )
                audit_events = db.execute(select(AuditEvent).order_by(AuditEvent.created_at.asc())).scalars().all()
                controls = trade_workflow_service.list_control_change_cases(db=db, current_user=current_user)

        self.assertTrue(payload["created"])
        self.assertEqual(payload["control_change"]["status"], "pending_review")
        self.assertIn(payload["control_change"]["risk_band"], {"high", "critical"})
        self.assertEqual(controls["count"], 1)
        self.assertIn("control_change.requested", [row.event_type for row in audit_events])

    def test_open_trade_automated_entry_uses_linked_account_oauth_client(self) -> None:
        from backend.core.database import Base
        from backend.models.saas import BrokerageLinkedAccount, TradeApprovalIntent, User
        from backend.schemas import OpenTradeRequest
        from backend.services import tenant_service, trade_service

        engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
        Session = sessionmaker(bind=engine, expire_on_commit=False, future=True)
        Base.metadata.create_all(bind=engine)

        fake_settings = SimpleNamespace(
            demo_tenant_slug="alpha-desk",
            demo_tenant_name="Alpha Desk",
            demo_tenant_plan="pro",
        )
        report = {
            "ticker": "SPY",
            "interval": "5m",
            "trade_decision": "VALID TRADE",
            "reject_reason": "",
            "event_risk": False,
            "option_plan": {
                "recommended_contract": None,
                "expected_underlying_target": 510.0,
                "invalidation_price": 496.0,
                "take_profit_1": 0.2,
                "take_profit_2": 0.35,
            },
        }
        position = {
            "suggested_contracts": 5,
            "total_position_cost": 1250.0,
            "max_risk_dollars": 100.0,
        }

        with (
            Session() as db,
            patch.object(tenant_service, "settings", fake_settings),
            patch.object(trade_service, "analyze_market", return_value={"report": report, "live_price": 501.2}),
            patch.object(trade_service, "_validate_directional_entry_request"),
            patch.object(trade_service, "_validate_option_execution_request", return_value={}),
            patch.object(trade_service, "_validate_capital_preservation_request", return_value={}),
            patch.object(trade_service, "_build_order_ticket_payload", return_value={"contract_symbol": "SPY240621C00510000"}),
            patch.object(trade_service.sdm, "calculate_position_sizing", return_value=position),
            patch.object(trade_service.sdm, "read_open_trades", return_value=pd.DataFrame()),
            patch.object(trade_service.sdm, "read_pending_orders", return_value=pd.DataFrame()),
            patch.object(trade_service.sdm, "read_closed_trades", return_value=pd.DataFrame()),
            patch.object(trade_service, "get_execution_adapter_for") as get_execution_adapter_for_mock,
        ):
            identity = tenant_service.ensure_demo_tenant_for_user(
                db,
                auth_subject="demo-trader",
                email="demo@example.test",
                name="Demo Trader",
                provider="local-demo",
                requested_tenant_slug="alpha-desk",
            )
            actor = db.execute(select(User).where(User.auth_subject == "demo-trader")).scalar_one()
            linked_account = BrokerageLinkedAccount(
                tenant=identity["active_tenant"],
                owner_user=actor,
                provider="alpaca",
                label="Client Paper",
                account_environment="paper",
                connection_status="connected",
                token_health="healthy",
                approval_policy="approval_required",
                oauth_access_token="oauth-token",
                metadata_json={
                    "automation": {
                        "client_auto_trading_opt_in": True,
                        "operator_auto_trading_enabled": True,
                        "account_size": 25000.0,
                        "risk_percent": 0.75,
                        "max_notional_per_trade": 1500.0,
                        "max_open_positions": 2,
                    }
                },
            )
            db.add(linked_account)
            db.commit()
            current_user = SimpleNamespace(
                tenant_id=identity["active_tenant"].id,
                tenant_slug="alpha-desk",
                tenant_name="Alpha Desk",
                tenant_status="active",
                tenant_plan="pro",
                auth_subject="demo-trader",
                user_id="demo-trader",
            )
            oauth_client = MagicMock()
            oauth_client.list_positions.return_value = []
            oauth_client.submit_order.return_value = {"id": "broker-order-2", "status": "accepted"}
            with patch.object(trade_service, "build_linked_account_execution_client", return_value=oauth_client):
                payload = trade_service.open_trade_from_request(
                    OpenTradeRequest(
                        ticker="SPY",
                        interval="5m",
                        horizon=5,
                        live_price=501.2,
                        account_size=25000,
                        risk_percent=0.75,
                        instrument_type="equity",
                        order_type="limit",
                        time_in_force="day",
                        limit_price=501.2,
                        account_target_type="linked_client",
                        linked_account_id=linked_account.id,
                        execution_mode="automated_entry",
                    ),
                    db=db,
                    current_user=current_user,
                )
            stored_intent = db.execute(select(TradeApprovalIntent)).scalar_one()

        self.assertFalse(payload["opened"])
        self.assertTrue(payload["automated_submitted"])
        self.assertEqual(payload["trade_intent"]["status"], "submitted")
        self.assertEqual(payload["trade_intent"]["execution_mode"], "automated_entry")
        self.assertTrue(payload["trade_intent"]["auto_submitted"])
        self.assertEqual(stored_intent.status, "submitted")
        oauth_client.list_positions.assert_called_once()
        oauth_client.submit_order.assert_called_once()
        get_execution_adapter_for_mock.assert_not_called()

    def test_open_trade_automated_entry_respects_linked_account_max_open_positions(self) -> None:
        from backend.core.database import Base
        from backend.models.saas import BrokerageLinkedAccount, User
        from backend.schemas import OpenTradeRequest
        from backend.services import tenant_service, trade_service
        from backend.services.exceptions import ValidationServiceError

        engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
        Session = sessionmaker(bind=engine, expire_on_commit=False, future=True)
        Base.metadata.create_all(bind=engine)

        fake_settings = SimpleNamespace(
            demo_tenant_slug="alpha-desk",
            demo_tenant_name="Alpha Desk",
            demo_tenant_plan="pro",
        )
        with (
            Session() as db,
            patch.object(tenant_service, "settings", fake_settings),
            patch.object(trade_service, "analyze_market", return_value={"report": {"ticker": "SPY", "trade_decision": "VALID TRADE", "reject_reason": "", "event_risk": False, "option_plan": {"recommended_contract": None}}, "live_price": 501.2}),
            patch.object(trade_service, "_validate_directional_entry_request"),
            patch.object(trade_service, "_validate_option_execution_request", return_value={}),
            patch.object(trade_service, "_validate_capital_preservation_request", return_value={}),
            patch.object(trade_service, "_build_order_ticket_payload", return_value={}),
            patch.object(trade_service, "_build_equity_position_preview", return_value={"suggested_contracts": 2, "total_position_cost": 840.0, "max_risk_dollars": 125.0}),
            patch.object(trade_service.sdm, "calculate_position_sizing", return_value={"suggested_contracts": 2, "total_position_cost": 840.0, "max_risk_dollars": 125.0}),
            patch.object(trade_service.sdm, "read_open_trades", return_value=pd.DataFrame()),
            patch.object(trade_service.sdm, "read_pending_orders", return_value=pd.DataFrame()),
            patch.object(trade_service.sdm, "read_closed_trades", return_value=pd.DataFrame()),
        ):
            identity = tenant_service.ensure_demo_tenant_for_user(
                db,
                auth_subject="demo-trader",
                email="demo@example.test",
                name="Demo Trader",
                provider="local-demo",
                requested_tenant_slug="alpha-desk",
            )
            actor = db.execute(select(User).where(User.auth_subject == "demo-trader")).scalar_one()
            linked_account = BrokerageLinkedAccount(
                tenant=identity["active_tenant"],
                owner_user=actor,
                provider="alpaca",
                label="Client Paper",
                account_environment="paper",
                connection_status="connected",
                token_health="healthy",
                approval_policy="approval_required",
                oauth_access_token="oauth-token",
                metadata_json={
                    "automation": {
                        "client_auto_trading_opt_in": True,
                        "operator_auto_trading_enabled": True,
                        "max_open_positions": 2,
                    }
                },
            )
            db.add(linked_account)
            db.commit()
            current_user = SimpleNamespace(
                tenant_id=identity["active_tenant"].id,
                tenant_slug="alpha-desk",
                tenant_name="Alpha Desk",
                tenant_status="active",
                tenant_plan="pro",
                auth_subject="demo-trader",
                user_id="demo-trader",
            )
            oauth_client = MagicMock()
            oauth_client.list_positions.return_value = [{"symbol": "QQQ"}, {"symbol": "IWM"}]
            oauth_client.submit_order.return_value = {"id": "should-not-submit", "status": "accepted"}
            with patch.object(trade_service, "build_linked_account_execution_client", return_value=oauth_client):
                with self.assertRaises(ValidationServiceError):
                    trade_service.open_trade_from_request(
                        OpenTradeRequest(
                            ticker="SPY",
                            interval="5m",
                            horizon=5,
                            live_price=501.2,
                            account_size=10000,
                            risk_percent=0.5,
                            instrument_type="equity",
                            order_type="limit",
                            time_in_force="day",
                            limit_price=501.2,
                            account_target_type="linked_client",
                            linked_account_id=linked_account.id,
                            execution_mode="automated_entry",
                        ),
                        db=db,
                        current_user=current_user,
                    )

        oauth_client.list_positions.assert_called_once()
        oauth_client.submit_order.assert_not_called()

    def test_trade_summary_includes_client_trade_intent_queue(self) -> None:
        from backend.core.database import Base
        from backend.models.saas import BrokerageLinkedAccount, TradeApprovalIntent, User
        from backend.services import portfolio_service, tenant_service, trade_service

        engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
        Session = sessionmaker(bind=engine, expire_on_commit=False, future=True)
        Base.metadata.create_all(bind=engine)

        fake_settings = SimpleNamespace(
            demo_tenant_slug="alpha-desk",
            demo_tenant_name="Alpha Desk",
            demo_tenant_plan="pro",
        )

        with (
            Session() as db,
            patch.object(tenant_service, "settings", fake_settings),
            patch.object(trade_service.sdm, "read_open_trades", return_value=pd.DataFrame()),
            patch.object(trade_service.sdm, "read_pending_orders", return_value=pd.DataFrame()),
            patch.object(trade_service.sdm, "read_closed_trades", return_value=pd.DataFrame()),
            patch.object(trade_service.sdm, "monitor_open_trades", return_value=pd.DataFrame()),
            patch.object(trade_service.sdm, "trade_summary", return_value={"total_realized_pnl": 0}),
            patch.object(trade_service, "get_pending_orders_snapshot", return_value={"items": [], "count": 0, "order_events": {"items": [], "count": 0, "status_counts": {}}}),
            patch.object(trade_service, "get_order_events_snapshot", return_value={"items": [], "count": 0, "status_counts": {}}),
            patch.object(trade_service, "get_order_lifecycle_health_snapshot", return_value={"summary": {"status": "healthy"}, "checks": []}),
            patch.object(trade_service, "_build_rollout_readiness_snapshot", return_value={"status": "collecting", "allows_live_rollout": False}),
            patch.object(portfolio_service, "_build_validation_snapshot", return_value={"scorecards": [], "route_quality": {}, "ranked_entry_rollout": {}}),
            patch.object(portfolio_service, "_build_attribution_summary", return_value={}),
            patch.object(portfolio_service, "_normalize_closed_trades_for_journal", return_value=pd.DataFrame()),
        ):
            identity = tenant_service.ensure_demo_tenant_for_user(
                db,
                auth_subject="demo-trader",
                email="demo@example.test",
                name="Demo Trader",
                provider="local-demo",
                requested_tenant_slug="alpha-desk",
            )
            actor = db.execute(select(User).where(User.auth_subject == "demo-trader")).scalar_one()
            linked_account = BrokerageLinkedAccount(
                tenant=identity["active_tenant"],
                owner_user=actor,
                provider="alpaca",
                label="Client Paper",
                account_environment="paper",
                connection_status="connected",
                token_health="healthy",
                approval_policy="approval_required",
                oauth_access_token="oauth-token",
            )
            pending_intent = TradeApprovalIntent(
                tenant=identity["active_tenant"],
                requester_user=actor,
                linked_account=linked_account,
                provider="alpaca",
                execution_lane="linked_client",
                status="pending_approval",
                ticker="SPY",
                instrument_type="equity",
                account_environment="paper",
                trade_id="trade-client-pending",
                order_id="order-client-pending",
            )
            submitted_intent = TradeApprovalIntent(
                tenant=identity["active_tenant"],
                requester_user=actor,
                approver_user=actor,
                linked_account=linked_account,
                provider="alpaca",
                execution_lane="linked_client",
                status="submitted",
                ticker="QQQ",
                instrument_type="equity",
                account_environment="paper",
                trade_id="trade-client-submitted",
                order_id="order-client-submitted",
            )
            db.add_all([linked_account, pending_intent, submitted_intent])
            db.commit()
            current_user = SimpleNamespace(
                tenant_id=identity["active_tenant"].id,
                tenant_slug="alpha-desk",
                tenant_name="Alpha Desk",
                tenant_status="active",
                tenant_plan="pro",
                auth_subject="demo-trader",
                user_id="demo-trader",
            )

            payload = trade_service.get_trade_summary(db=db, current_user=current_user)

        self.assertIn("client_trade_intents", payload)
        self.assertEqual(payload["client_trade_intents"]["count"], 2)
        self.assertEqual(payload["client_trade_intents"]["pending_approval_count"], 1)
        self.assertEqual(payload["client_trade_intents"]["submitted_count"], 1)
        self.assertEqual(payload["client_trade_intents"]["items"][0]["status"], "submitted")
        self.assertEqual(payload["client_trade_intents"]["items"][1]["status"], "pending_approval")

    def test_trade_summary_includes_client_automation_summary(self) -> None:
        from backend.core.database import Base
        from backend.models.saas import BrokerageLinkedAccount, TradeApprovalIntent, User
        from backend.services import portfolio_service, tenant_service, trade_service

        engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
        Session = sessionmaker(bind=engine, expire_on_commit=False, future=True)
        Base.metadata.create_all(bind=engine)

        fake_settings = SimpleNamespace(
            demo_tenant_slug="alpha-desk",
            demo_tenant_name="Alpha Desk",
            demo_tenant_plan="pro",
        )

        with (
            Session() as db,
            patch.object(tenant_service, "settings", fake_settings),
            patch.object(trade_service.sdm, "read_open_trades", return_value=pd.DataFrame()),
            patch.object(trade_service.sdm, "read_pending_orders", return_value=pd.DataFrame()),
            patch.object(trade_service.sdm, "read_closed_trades", return_value=pd.DataFrame()),
            patch.object(trade_service.sdm, "monitor_open_trades", return_value=pd.DataFrame()),
            patch.object(trade_service.sdm, "trade_summary", return_value={"total_realized_pnl": 0}),
            patch.object(trade_service, "get_pending_orders_snapshot", return_value={"items": [], "count": 0, "order_events": {"items": [], "count": 0, "status_counts": {}}}),
            patch.object(trade_service, "get_order_events_snapshot", return_value={"items": [], "count": 0, "status_counts": {}}),
            patch.object(trade_service, "get_order_lifecycle_health_snapshot", return_value={"summary": {"status": "healthy"}, "checks": []}),
            patch.object(trade_service, "_build_rollout_readiness_snapshot", return_value={"status": "collecting", "allows_live_rollout": False}),
            patch.object(portfolio_service, "_build_validation_snapshot", return_value={"scorecards": [], "route_quality": {}, "ranked_entry_rollout": {}}),
            patch.object(portfolio_service, "_build_attribution_summary", return_value={}),
            patch.object(portfolio_service, "_normalize_closed_trades_for_journal", return_value=pd.DataFrame()),
        ):
            identity = tenant_service.ensure_demo_tenant_for_user(
                db,
                auth_subject="demo-trader",
                email="demo@example.test",
                name="Demo Trader",
                provider="local-demo",
                requested_tenant_slug="alpha-desk",
            )
            actor = db.execute(select(User).where(User.auth_subject == "demo-trader")).scalar_one()
            eligible_account = BrokerageLinkedAccount(
                tenant=identity["active_tenant"],
                owner_user=actor,
                provider="alpaca",
                label="Client Paper",
                account_environment="paper",
                connection_status="connected",
                token_health="healthy",
                approval_policy="approval_required",
                oauth_access_token="oauth-token",
                metadata_json={
                    "automation": {
                        "client_auto_trading_opt_in": True,
                        "operator_auto_trading_enabled": True,
                        "last_automated_submission_at": "2026-04-23T13:35:00+00:00",
                        "last_automated_order": {"ticker": "SPY", "broker_order_id": "broker-order-1"},
                    }
                },
            )
            blocked_account = BrokerageLinkedAccount(
                tenant=identity["active_tenant"],
                owner_user=actor,
                provider="alpaca",
                label="Client Live",
                account_environment="live",
                connection_status="connected",
                token_health="healthy",
                approval_policy="approval_required",
                oauth_access_token="oauth-token-live",
                metadata_json={
                    "automation": {
                        "client_auto_trading_opt_in": True,
                        "operator_auto_trading_enabled": True,
                    }
                },
            )
            automated_intent = TradeApprovalIntent(
                tenant=identity["active_tenant"],
                requester_user=actor,
                approver_user=actor,
                linked_account=eligible_account,
                provider="alpaca",
                execution_lane="linked_client",
                status="submitted",
                ticker="SPY",
                instrument_type="equity",
                account_environment="paper",
                trade_id="trade-client-automated",
                order_id="order-client-automated",
                metadata_json={"execution_mode": "automated_entry", "auto_submitted": True},
            )
            db.add_all([eligible_account, blocked_account, automated_intent])
            db.commit()
            current_user = SimpleNamespace(
                tenant_id=identity["active_tenant"].id,
                tenant_slug="alpha-desk",
                tenant_name="Alpha Desk",
                tenant_status="active",
                tenant_plan="pro",
                auth_subject="demo-trader",
                user_id="demo-trader",
            )

            payload = trade_service.get_trade_summary(db=db, current_user=current_user)

        self.assertIn("client_automation", payload)
        self.assertEqual(payload["client_automation"]["eligible_linked_account_count"], 1)
        self.assertEqual(payload["client_automation"]["automated_linked_account_count"], 2)
        self.assertEqual(payload["client_automation"]["blocked_linked_account_count"], 1)
        self.assertEqual(payload["client_automation"]["last_automated_client_order"]["account_label"], "Client Paper")
        self.assertEqual(payload["client_trade_intents"]["automated_entry_count"], 1)

    def test_fill_pending_order_records_filled_event(self) -> None:
        from backend.core.database import Base
        from backend.models.saas import OrderEventRecord
        from backend.schemas import FillOrderRequest
        from backend.services import tenant_service, trade_service

        engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
        Session = sessionmaker(bind=engine, expire_on_commit=False, future=True)
        Base.metadata.create_all(bind=engine)

        fake_settings = SimpleNamespace(
            demo_tenant_slug="alpha-desk",
            demo_tenant_name="Alpha Desk",
            demo_tenant_plan="pro",
        )
        pending_order = {
            "order_id": "order-live-1",
            "trade_id": "trade-live-1",
            "ticker": "SPY",
            "submitted_at": "2026-04-16T10:00:00+00:00",
            "updated_at": "2026-04-16T10:01:00+00:00",
            "order_type": "limit",
            "time_in_force": "day_ext",
            "status": "PENDING",
            "book_state": "pending",
        }
        filled_record = {
            **pending_order,
            "opened_at": "2026-04-16T10:02:00+00:00",
            "status": "OPEN",
            "book_state": "open",
        }

        with (
            Session() as db,
            patch.object(tenant_service, "settings", fake_settings),
            patch.object(trade_service.sdm, "read_pending_orders", return_value=pd.DataFrame([pending_order])),
            patch.object(trade_service.sdm, "fill_pending_order", return_value=filled_record),
        ):
            identity = tenant_service.ensure_demo_tenant_for_user(
                db,
                auth_subject="demo-trader",
                email="demo@example.test",
                name="Demo Trader",
                provider="local-demo",
                requested_tenant_slug="alpha-desk",
            )
            current_user = SimpleNamespace(
                tenant_id=identity["active_tenant"].id,
                tenant_slug="alpha-desk",
                tenant_name="Alpha Desk",
                tenant_status="active",
                tenant_plan="pro",
                auth_subject="demo-trader",
                user_id="demo-trader",
            )

            payload = trade_service.fill_pending_order_from_request(
                "order-live-1",
                FillOrderRequest(live_price=502.5),
                db=db,
                current_user=current_user,
            )
            stored_events = db.execute(select(OrderEventRecord).order_by(OrderEventRecord.created_at.asc())).scalars().all()

        self.assertTrue(payload["filled"])
        self.assertEqual(payload["latest_order_event"]["status"], "filled")
        self.assertEqual(len(stored_events), 1)
        self.assertEqual(stored_events[0].event_key, "order.filled")

    def test_close_trade_by_index_preserves_current_route_metadata_for_partial_and_full_closes(self) -> None:
        from backend import stock_direction_model as sdm

        with _workspace_tempdir() as temp_dir:
            temp_root = Path(temp_dir)
            open_path = temp_root / "open_trades.csv"
            closed_path = temp_root / "closed_trades.csv"

            pd.DataFrame(
                [
                    {
                        "trade_id": "legacy-1",
                        "ticker": "QQQ",
                        "realized_pnl": 10.0,
                        "status": "CLOSED",
                    }
                ]
            ).to_csv(closed_path, index=False)

            sdm.append_open_trade(
                {
                    "trade_id": "trade-current-1",
                    "order_id": "order-current-1",
                    "opened_at": "2026-04-22T16:00:00+00:00",
                    "ticker": "SPY",
                    "interval": "5m",
                    "verdict": "BULLISH",
                    "instrument_type": "equity",
                    "suggested_contracts": 10.0,
                    "position_cost": 1000.0,
                    "max_risk_dollars": 100.0,
                    "contract_mid_at_open": 1.0,
                    "live_price_at_open": 100.0,
                    "route_family": "current",
                    "route_version": "ranked_entry_v1",
                    "automation_entry_reason": "ranked_candidate",
                    "route_correlation_id": "corr-current-1",
                    "thesis_direction": "BULLISH",
                    "directional_exposure": "bullish",
                    "validation_sample_bucket": "current_route",
                    "status": "OPEN",
                },
                file_path=open_path,
            )

            sdm.close_trade_by_index(
                0,
                close_underlying_price=102.0,
                close_contract_mid=1.02,
                close_fraction=0.5,
                file_path_open=open_path,
                file_path_closed=closed_path,
            )

            partial_closed = sdm.read_closed_trades(closed_path)
            partial_row = partial_closed.iloc[-1].to_dict()
            remaining_open = sdm.read_open_trades(open_path)

            self.assertEqual(partial_row["route_family"], "current")
            self.assertEqual(partial_row["route_version"], "ranked_entry_v1")
            self.assertEqual(partial_row["automation_entry_reason"], "ranked_candidate")
            self.assertEqual(partial_row["route_correlation_id"], "corr-current-1")
            self.assertEqual(partial_row["validation_sample_bucket"], "current_route")
            self.assertEqual(partial_row["status"], "PARTIAL")
            self.assertEqual(remaining_open.iloc[0]["route_family"], "current")
            self.assertEqual(remaining_open.iloc[0]["route_correlation_id"], "corr-current-1")

            sdm.close_trade_by_index(
                0,
                close_underlying_price=103.0,
                close_contract_mid=1.03,
                close_fraction=1.0,
                file_path_open=open_path,
                file_path_closed=closed_path,
            )

            fully_closed = sdm.read_closed_trades(closed_path)
            final_row = fully_closed.iloc[-1].to_dict()

            self.assertEqual(final_row["route_family"], "current")
            self.assertEqual(final_row["route_version"], "ranked_entry_v1")
            self.assertEqual(final_row["automation_entry_reason"], "ranked_candidate")
            self.assertEqual(final_row["route_correlation_id"], "corr-current-1")
            self.assertEqual(final_row["thesis_direction"], "BULLISH")
            self.assertEqual(final_row["directional_exposure"], "bullish")
            self.assertEqual(final_row["validation_sample_bucket"], "current_route")
            self.assertEqual(final_row["status"], "CLOSED")

    def test_alpaca_paper_adapter_creates_broker_backed_pending_order(self) -> None:
        from backend.schemas import OpenTradeRequest
        from backend.services.execution.alpaca_paper_adapter import AlpacaPaperExecutionAdapter

        report = {
            "ticker": "SPY",
            "interval": "5m",
            "verdict": "BULLISH",
            "alignment_label": "BULLISH ALIGNMENT",
            "conviction_label": "HIGH CONVICTION CALL",
            "setup_score": 88.0,
            "setup_grade": "A setup",
            "trade_decision": "VALID TRADE",
            "reject_reason": "",
            "event_risk": False,
            "event_label": "NO EVENT RISK",
            "event_reason": "",
            "next_event_name": "",
            "next_event_date": "",
            "option_plan": {
                "recommended_contract": None,
                "expected_underlying_target": 510.0,
                "invalidation_price": 496.0,
                "take_profit_1": 0.2,
                "take_profit_2": 0.35,
            },
        }
        position = {
            "suggested_contracts": 2,
            "total_position_cost": 1001.0,
            "max_risk_dollars": 100.0,
        }
        fake_client = SimpleNamespace(
            submit_order=MagicMock(
                return_value={
                    "id": "broker-order-1",
                    "client_order_id": "internal-order-1",
                    "status": "new",
                    "qty": "2",
                    "filled_qty": "0",
                    "filled_avg_price": None,
                    "asset_class": "us_equity",
                    "submitted_at": "2026-04-18T13:35:00Z",
                    "updated_at": "2026-04-18T13:35:02Z",
                }
            ),
            get_asset=MagicMock(return_value={"class": "us_equity", "fractionable": True}),
        )
        pending_rows: list[dict[str, object]] = []

        def append_pending_order_side_effect(row: dict[str, object], file_path=None) -> None:
            pending_rows.clear()
            pending_rows.append(dict(row))

        def read_pending_orders_side_effect(file_path=None) -> pd.DataFrame:
            return pd.DataFrame(pending_rows)

        with (
            patch("backend.services.execution.alpaca_paper_adapter.settings", SimpleNamespace(alpaca_api_key_id="key", alpaca_api_secret_key="secret")),
            patch("backend.services.execution.alpaca_paper_adapter.sdm.append_pending_order", side_effect=append_pending_order_side_effect),
            patch("backend.services.execution.alpaca_paper_adapter.sdm.read_pending_orders", side_effect=read_pending_orders_side_effect),
            patch("backend.services.execution.alpaca_paper_adapter.sdm.read_open_trades", return_value=pd.DataFrame()),
            patch("backend.services.execution.alpaca_paper_adapter.sdm.read_closed_trades", return_value=pd.DataFrame()),
        ):
            adapter = AlpacaPaperExecutionAdapter(client=fake_client)
            result = adapter.submit_order(
                request=OpenTradeRequest(
                    ticker="SPY",
                    interval="5m",
                    horizon=5,
                    live_price=500.5,
                    account_size=10000,
                    risk_percent=0.5,
                    instrument_type="equity",
                    order_type="limit",
                    time_in_force="day",
                    limit_price=500.5,
                    extended_hours=False,
                ),
                report=report,
                live_price=500.5,
                position=position,
                trade_id="trade-internal-1",
                order_id="internal-order-1",
                order_ticket={
                    "instrument_type": "equity",
                    "contract_symbol": "EQUITY:SPY",
                    "order_type": "limit",
                    "time_in_force": "day",
                    "limit_price": 500.5,
                    "extended_hours": False,
                    "route_family": "current",
                    "route_version": "ranked_entry_v1",
                    "route_correlation_id": "corr-pending-1",
                    "automation_entry_reason": "ranked_candidate",
                    "thesis_direction": "BULLISH",
                },
            )

        self.assertFalse(result.position_opened)
        self.assertEqual(result.broker_name, "alpaca_paper")
        self.assertEqual(result.broker_order_id, "broker-order-1")
        self.assertEqual(result.pending_order["broker_status"], "new")
        self.assertTrue(result.pending_order["broker_fractionable"])
        self.assertEqual(result.pending_order["route_family"], "current")
        self.assertEqual(result.pending_order["route_version"], "ranked_entry_v1")
        self.assertEqual(result.pending_order["route_correlation_id"], "corr-pending-1")
        self.assertEqual(result.pending_order["automation_entry_reason"], "ranked_candidate")
        self.assertEqual(result.pending_order["validation_sample_bucket"], "current_route")
        self.assertEqual(len(pending_rows), 1)

    def test_alpaca_paper_adapter_persists_immediate_fill_with_route_metadata(self) -> None:
        from backend.schemas import OpenTradeRequest
        from backend.services.execution.alpaca_paper_adapter import AlpacaPaperExecutionAdapter

        report = {
            "ticker": "SPY",
            "interval": "5m",
            "verdict": "BULLISH",
            "alignment_label": "BULLISH ALIGNMENT",
            "conviction_label": "HIGH CONVICTION CALL",
            "setup_score": 88.0,
            "setup_grade": "A setup",
            "trade_decision": "VALID TRADE",
            "reject_reason": "",
            "event_risk": False,
            "event_label": "NO EVENT RISK",
            "event_reason": "",
            "next_event_name": "",
            "next_event_date": "",
            "option_plan": {
                "recommended_contract": None,
                "expected_underlying_target": 510.0,
                "invalidation_price": 496.0,
                "take_profit_1": 0.2,
                "take_profit_2": 0.35,
            },
        }
        position = {
            "suggested_contracts": 2,
            "total_position_cost": 1001.0,
            "max_risk_dollars": 100.0,
        }
        fake_client = SimpleNamespace(
            submit_order=MagicMock(
                return_value={
                    "id": "broker-order-2",
                    "client_order_id": "internal-order-2",
                    "status": "filled",
                    "qty": "2",
                    "filled_qty": "2",
                    "filled_avg_price": "501.25",
                    "asset_class": "us_equity",
                    "submitted_at": "2026-04-18T13:36:00Z",
                    "updated_at": "2026-04-18T13:36:02Z",
                }
            ),
            get_asset=MagicMock(return_value={"class": "us_equity", "fractionable": True}),
        )
        open_rows: list[dict[str, object]] = []

        def append_open_trade_side_effect(row: dict[str, object], file_path=None) -> None:
            open_rows.clear()
            open_rows.append(dict(row))

        def read_open_trades_side_effect(file_path=None) -> pd.DataFrame:
            return pd.DataFrame(open_rows)

        with (
            patch("backend.services.execution.alpaca_paper_adapter.settings", SimpleNamespace(alpaca_api_key_id="key", alpaca_api_secret_key="secret")),
            patch("backend.services.execution.alpaca_paper_adapter.sdm.append_open_trade", side_effect=append_open_trade_side_effect),
            patch("backend.services.execution.alpaca_paper_adapter.sdm.read_open_trades", side_effect=read_open_trades_side_effect),
            patch("backend.services.execution.alpaca_paper_adapter.sdm.read_pending_orders", return_value=pd.DataFrame()),
            patch("backend.services.execution.alpaca_paper_adapter.sdm.read_closed_trades", return_value=pd.DataFrame()),
        ):
            adapter = AlpacaPaperExecutionAdapter(client=fake_client)
            result = adapter.submit_order(
                request=OpenTradeRequest(
                    ticker="SPY",
                    interval="5m",
                    horizon=5,
                    live_price=500.5,
                    account_size=10000,
                    risk_percent=0.5,
                    instrument_type="equity",
                    order_type="market",
                    time_in_force="day",
                ),
                report=report,
                live_price=500.5,
                position=position,
                trade_id="trade-internal-2",
                order_id="internal-order-2",
                order_ticket={
                    "instrument_type": "equity",
                    "contract_symbol": "EQUITY:SPY",
                    "order_type": "market",
                    "time_in_force": "day",
                    "route_family": "current",
                    "route_version": "ranked_entry_v1",
                    "route_correlation_id": "corr-open-1",
                    "automation_entry_reason": "ranked_candidate",
                    "thesis_direction": "BULLISH",
                },
            )

        self.assertTrue(result.position_opened)
        self.assertEqual(result.record["route_correlation_id"], "corr-open-1")
        self.assertEqual(result.record["validation_sample_bucket"], "current_route")
        self.assertEqual(result.record["broker_status"], "filled")
        self.assertEqual(len(open_rows), 1)

    def test_build_alpaca_equity_order_payload_supports_fractional_qty(self) -> None:
        from backend.schemas import OpenTradeRequest
        from backend.services.execution.mappers import build_alpaca_equity_order_payload

        payload = build_alpaca_equity_order_payload(
            OpenTradeRequest(
                ticker="SPY",
                interval="5m",
                horizon=5,
                live_price=100.0,
                account_size=10.0,
                risk_percent=0.5,
                instrument_type="equity",
                order_type="limit",
                time_in_force="day",
                limit_price=100.0,
                fractional_shares_only=True,
            ),
            ticker="SPY",
            quantity=0.125,
            client_order_id="tiny-order-1",
        )

        self.assertEqual(payload["qty"], "0.125")
        self.assertEqual(payload["client_order_id"], "tiny-order-1")

    def test_build_alpaca_equity_order_payload_supports_extended_hours_limit_orders(self) -> None:
        from backend.schemas import OpenTradeRequest
        from backend.services.exceptions import ValidationServiceError
        from backend.services.execution.mappers import build_alpaca_equity_order_payload

        payload = build_alpaca_equity_order_payload(
            OpenTradeRequest(
                ticker="SPY",
                interval="5m",
                horizon=5,
                live_price=100.0,
                account_size=1000.0,
                risk_percent=1.0,
                instrument_type="equity",
                order_type="limit",
                time_in_force="day_ext",
                limit_price=100.25,
                extended_hours=True,
            ),
            ticker="SPY",
            quantity=2,
            client_order_id="ext-order-1",
        )

        self.assertEqual(payload["time_in_force"], "day")
        self.assertTrue(payload["extended_hours"])
        self.assertEqual(payload["limit_price"], "100.25")

        with self.assertRaises(ValidationServiceError):
            build_alpaca_equity_order_payload(
                OpenTradeRequest(
                    ticker="SPY",
                    interval="5m",
                    horizon=5,
                    live_price=100.0,
                    account_size=1000.0,
                    risk_percent=1.0,
                    instrument_type="equity",
                    order_type="market",
                    time_in_force="day_ext",
                    extended_hours=True,
                ),
                ticker="SPY",
                quantity=2,
            )

    def test_equity_position_preview_supports_fractional_tiny_account_sizing(self) -> None:
        from backend.services import trade_service

        report = {
            "trade_decision": "VALID TRADE",
            "reject_reason": "",
            "option_plan": {
                "invalidation_price": 90.0,
            },
            "forecast": {
                "regime_strength_score": 0.5,
            },
        }

        preview = trade_service._build_equity_position_preview(
            report,
            100.0,
            10.0,
            50.0,
            fractional_shares_only=True,
            max_notional_per_trade=5.0,
        )

        self.assertAlmostEqual(preview["suggested_contracts"], 0.05, places=3)
        self.assertAlmostEqual(preview["total_position_cost"], 5.0, places=3)
        self.assertTrue(preview["affordable"])
        self.assertTrue(preview["fractional_shares_only"])

    def test_execution_registry_supports_alpaca_paper(self) -> None:
        from backend.services.execution.alpaca_paper_adapter import AlpacaPaperExecutionAdapter
        from backend.services.execution import provider_registry

        with patch.object(provider_registry, "settings", SimpleNamespace(execution_adapter="alpaca_paper")):
            provider_registry.get_execution_adapter.cache_clear()
            adapter = provider_registry.get_execution_adapter()
            provider_registry.get_execution_adapter.cache_clear()

        self.assertIsInstance(adapter, AlpacaPaperExecutionAdapter)

    def test_execution_registry_supports_alpaca_live(self) -> None:
        from backend.services.execution.alpaca_live_adapter import AlpacaLiveExecutionAdapter
        from backend.services.execution import provider_registry

        with patch.object(provider_registry, "settings", SimpleNamespace(execution_adapter="alpaca_live")):
            provider_registry.get_execution_adapter.cache_clear()
            adapter = provider_registry.get_execution_adapter()
            provider_registry.get_execution_adapter.cache_clear()

        self.assertIsInstance(adapter, AlpacaLiveExecutionAdapter)

    def test_alpaca_paper_adapter_syncs_filled_order_with_slippage(self) -> None:
        from backend.services.execution.alpaca_paper_adapter import AlpacaPaperExecutionAdapter

        pending_order = {
            "trade_id": "trade-internal-1",
            "order_id": "internal-order-1",
            "ticker": "SPY",
            "broker_order_id": "broker-order-1",
            "broker_status": "new",
            "limit_price": 500.5,
            "live_price_at_submit": 500.25,
            "route_family": "current",
            "route_version": "ranked_entry_v1",
            "route_correlation_id": "corr-sync-1",
            "validation_sample_bucket": "current_route",
        }
        filled_record = {
            "trade_id": "trade-internal-1",
            "order_id": "internal-order-1",
            "ticker": "SPY",
            "status": "OPEN",
            "order_type": "limit",
            "time_in_force": "day",
            "route_family": "current",
            "route_version": "ranked_entry_v1",
            "route_correlation_id": "corr-sync-1",
            "validation_sample_bucket": "current_route",
        }
        fake_client = SimpleNamespace(
            get_order=MagicMock(
                return_value={
                    "id": "broker-order-1",
                    "status": "filled",
                    "qty": "2",
                    "filled_qty": "2",
                    "filled_avg_price": "501.25",
                    "asset_class": "us_equity",
                    "updated_at": "2026-04-18T14:05:00Z",
                }
            ),
        )
        open_rows: list[dict[str, object]] = []

        def update_open_trade_side_effect(updates: dict[str, object], **_: object) -> dict[str, object]:
            open_rows.clear()
            open_rows.append(dict(updates))
            return dict(updates)

        def read_open_trades_side_effect(file_path=None) -> pd.DataFrame:
            return pd.DataFrame(open_rows)

        with (
            patch("backend.services.execution.alpaca_paper_adapter.settings", SimpleNamespace(alpaca_api_key_id="key", alpaca_api_secret_key="secret")),
            patch("backend.services.execution.alpaca_paper_adapter.sdm.fill_pending_order", return_value=filled_record),
            patch("backend.services.execution.alpaca_paper_adapter.sdm.update_open_trade", side_effect=update_open_trade_side_effect),
            patch("backend.services.execution.alpaca_paper_adapter.sdm.read_open_trades", side_effect=read_open_trades_side_effect),
            patch("backend.services.execution.alpaca_paper_adapter.sdm.read_pending_orders", return_value=pd.DataFrame()),
            patch("backend.services.execution.alpaca_paper_adapter.sdm.read_closed_trades", return_value=pd.DataFrame()),
        ):
            adapter = AlpacaPaperExecutionAdapter(client=fake_client)
            result = adapter.sync_order(pending_order=pending_order)

        self.assertEqual(result.state, "filled")
        self.assertEqual(result.broker_status, "filled")
        self.assertAlmostEqual(result.slippage_dollars, 0.75, places=4)
        self.assertAlmostEqual(result.slippage_bps, 14.99, places=2)
        self.assertEqual(result.opened_record["expected_fill_price"], 500.5)
        self.assertEqual(result.opened_record["actual_fill_price"], 501.25)
        self.assertEqual(result.opened_record["route_correlation_id"], "corr-sync-1")
        self.assertEqual(len(open_rows), 1)

    def test_alpaca_paper_adapter_close_position_persists_current_route_metadata(self) -> None:
        from backend.schemas import CloseTradeRequest
        from backend.services.execution.alpaca_paper_adapter import AlpacaPaperExecutionAdapter

        target_trade = {
            "trade_id": "trade-close-1",
            "order_id": "order-close-1",
            "ticker": "SPY",
            "instrument_type": "equity",
            "suggested_contracts": 2.0,
            "route_family": "current",
            "route_version": "ranked_entry_v1",
            "route_correlation_id": "corr-close-1",
            "automation_entry_reason": "ranked_candidate",
            "thesis_direction": "BULLISH",
            "directional_exposure": "bullish",
            "validation_sample_bucket": "current_route",
        }
        fake_client = SimpleNamespace(
            submit_order=MagicMock(
                return_value={
                    "id": "broker-close-1",
                    "status": "filled",
                    "filled_avg_price": "502.50",
                }
            ),
        )
        open_rows = [dict(target_trade)]
        closed_rows: list[dict[str, object]] = []

        def close_trade_by_index_side_effect(
            trade_index: int,
            close_underlying_price: float,
            close_contract_mid: float,
            close_fraction: float = 1.0,
            close_updates: dict[str, object] | None = None,
            file_path_open=None,
            file_path_closed=None,
        ) -> dict[str, object]:
            trade = dict(open_rows[trade_index])
            remaining_contracts = float(trade["suggested_contracts"]) - 1.0
            open_rows[trade_index] = {
                **trade,
                "suggested_contracts": remaining_contracts,
            }
            closed_record = {
                **trade,
                "closed_at": "2026-04-20T14:10:00+00:00",
                "closed_contracts": 1.0,
                "remaining_contracts_after_close": remaining_contracts,
                "close_fraction": close_fraction,
                "status": "PARTIAL",
                "contract_mid_at_close": close_contract_mid,
            }
            if close_updates:
                closed_record.update(dict(close_updates))
            closed_rows.append(dict(closed_record))
            return closed_record

        with (
            patch("backend.services.execution.alpaca_paper_adapter.settings", SimpleNamespace(alpaca_api_key_id="key", alpaca_api_secret_key="secret")),
            patch("backend.services.execution.alpaca_paper_adapter.sdm.close_trade_by_index", side_effect=close_trade_by_index_side_effect),
            patch("backend.services.execution.alpaca_paper_adapter.sdm.read_open_trades", side_effect=lambda file_path=None: pd.DataFrame(open_rows)),
            patch("backend.services.execution.alpaca_paper_adapter.sdm.read_closed_trades", side_effect=lambda file_path=None: pd.DataFrame(closed_rows)),
            patch("backend.services.execution.alpaca_paper_adapter.sdm.read_pending_orders", return_value=pd.DataFrame()),
        ):
            adapter = AlpacaPaperExecutionAdapter(client=fake_client)
            result = adapter.close_position(
                request=CloseTradeRequest(
                    trade_index=0,
                    close_underlying_price=501.0,
                    close_contract_mid=5.01,
                    close_fraction=0.5,
                ),
                target_trade=target_trade,
            )

        self.assertEqual(result.closed_trade["route_family"], "current")
        self.assertEqual(result.closed_trade["route_version"], "ranked_entry_v1")
        self.assertEqual(result.closed_trade["route_correlation_id"], "corr-close-1")
        self.assertEqual(result.closed_trade["validation_sample_bucket"], "current_route")
        self.assertEqual(result.closed_trade["broker_close_order_id"], "broker-close-1")
        self.assertEqual(result.closed_trade["broker_close_status"], "filled")
        self.assertEqual(len(closed_rows), 1)

    def test_alpaca_paper_adapter_close_position_persists_full_close_to_closed_rows(self) -> None:
        from backend.schemas import CloseTradeRequest
        from backend.services.execution.alpaca_paper_adapter import AlpacaPaperExecutionAdapter

        target_trade = {
            "trade_id": "trade-close-2",
            "order_id": "order-close-2",
            "ticker": "SPY",
            "instrument_type": "equity",
            "suggested_contracts": 1.0,
            "route_family": "current",
            "route_version": "ranked_entry_v1",
            "route_correlation_id": "corr-close-2",
            "automation_entry_reason": "ranked_candidate",
            "thesis_direction": "BULLISH",
            "directional_exposure": "bullish",
            "validation_sample_bucket": "current_route",
        }
        fake_client = SimpleNamespace(
            close_position=MagicMock(
                return_value={
                    "id": "broker-close-2",
                    "status": "filled",
                    "filled_avg_price": "503.00",
                }
            )
        )
        open_rows = [dict(target_trade)]
        closed_rows: list[dict[str, object]] = []

        def close_trade_by_index_side_effect(
            trade_index: int,
            close_underlying_price: float,
            close_contract_mid: float,
            close_fraction: float = 1.0,
            close_updates: dict[str, object] | None = None,
            file_path_open=None,
            file_path_closed=None,
        ) -> dict[str, object]:
            trade = dict(open_rows[trade_index])
            open_rows.clear()
            closed_record = {
                **trade,
                "closed_at": "2026-04-20T14:15:00+00:00",
                "closed_contracts": 1.0,
                "remaining_contracts_after_close": 0.0,
                "close_fraction": close_fraction,
                "status": "CLOSED",
                "contract_mid_at_close": close_contract_mid,
            }
            if close_updates:
                closed_record.update(dict(close_updates))
            closed_rows.append(dict(closed_record))
            return closed_record

        with (
            patch("backend.services.execution.alpaca_paper_adapter.settings", SimpleNamespace(alpaca_api_key_id="key", alpaca_api_secret_key="secret")),
            patch("backend.services.execution.alpaca_paper_adapter.sdm.close_trade_by_index", side_effect=close_trade_by_index_side_effect),
            patch("backend.services.execution.alpaca_paper_adapter.sdm.read_open_trades", side_effect=lambda file_path=None: pd.DataFrame(open_rows)),
            patch("backend.services.execution.alpaca_paper_adapter.sdm.read_closed_trades", side_effect=lambda file_path=None: pd.DataFrame(closed_rows)),
            patch("backend.services.execution.alpaca_paper_adapter.sdm.read_pending_orders", return_value=pd.DataFrame()),
        ):
            adapter = AlpacaPaperExecutionAdapter(client=fake_client)
            result = adapter.close_position(
                request=CloseTradeRequest(
                    trade_index=0,
                    close_underlying_price=503.0,
                    close_contract_mid=5.03,
                    close_fraction=1.0,
                ),
                target_trade=target_trade,
            )

        self.assertEqual(result.closed_trade["status"], "CLOSED")
        self.assertEqual(result.closed_trade["route_correlation_id"], "corr-close-2")
        self.assertEqual(result.closed_trade["remaining_contracts_after_close"], 0.0)
        self.assertEqual(result.closed_trade["broker_close_order_id"], "broker-close-2")
        self.assertEqual(len(closed_rows), 1)

    def test_open_trade_payload_includes_execution_metadata(self) -> None:
        from backend.core.database import Base
        from backend.schemas import OpenTradeRequest
        from backend.services import tenant_service, trade_service
        from backend.services.execution.types import SubmitOrderResult

        engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
        Session = sessionmaker(bind=engine, expire_on_commit=False, future=True)
        Base.metadata.create_all(bind=engine)

        fake_settings = SimpleNamespace(
            demo_tenant_slug="alpha-desk",
            demo_tenant_name="Alpha Desk",
            demo_tenant_plan="pro",
        )
        report = {
            "ticker": "SPY",
            "interval": "5m",
            "trade_decision": "VALID TRADE",
            "reject_reason": "",
            "option_plan": {
                "recommended_contract": None,
                "expected_underlying_target": 510.0,
                "invalidation_price": 496.0,
                "take_profit_1": 0.2,
                "take_profit_2": 0.35,
            },
        }
        position = {
            "suggested_contracts": 2,
            "total_position_cost": 1001.0,
            "max_risk_dollars": 100.0,
        }
        broker_record = {
            "trade_id": "trade-paper-1",
            "order_id": "internal-order-1",
            "broker_name": "alpaca_paper",
            "broker_order_id": "broker-order-1",
            "broker_status": "new",
            "ticker": "SPY",
            "status": "PENDING",
        }
        fake_adapter = MagicMock()
        fake_adapter.adapter_name = "alpaca_paper"
        fake_adapter.submit_order.return_value = SubmitOrderResult(
            position_opened=False,
            record=broker_record,
            pending_order=broker_record,
            broker_name="alpaca_paper",
            broker_order_id="broker-order-1",
            broker_status="new",
            broker_response={"id": "broker-order-1", "status": "new"},
        )

        with (
            Session() as db,
            patch.object(tenant_service, "settings", fake_settings),
            patch.object(trade_service, "analyze_market", return_value={"report": report, "live_price": 500.5}),
            patch.object(trade_service, "_build_equity_position_preview", return_value=position),
            patch.object(trade_service.sdm, "read_open_trades", return_value=pd.DataFrame()),
            patch.object(trade_service.sdm, "read_pending_orders", return_value=pd.DataFrame()),
            patch.object(trade_service.sdm, "read_closed_trades", return_value=pd.DataFrame()),
            patch.object(trade_service, "get_execution_adapter", return_value=fake_adapter),
        ):
            identity = tenant_service.ensure_demo_tenant_for_user(
                db,
                auth_subject="demo-trader",
                email="demo@example.test",
                name="Demo Trader",
                provider="local-demo",
                requested_tenant_slug="alpha-desk",
            )
            current_user = SimpleNamespace(
                tenant_id=identity["active_tenant"].id,
                tenant_slug="alpha-desk",
                tenant_name="Alpha Desk",
                tenant_status="active",
                tenant_plan="pro",
                auth_subject="demo-trader",
                user_id="demo-trader",
            )

            payload = trade_service.open_trade_from_request(
                OpenTradeRequest(
                    ticker="SPY",
                    interval="5m",
                    horizon=5,
                    live_price=500.5,
                    account_size=10000,
                    risk_percent=0.5,
                    instrument_type="equity",
                    order_type="limit",
                    time_in_force="day",
                    limit_price=500.5,
                ),
                db=db,
                current_user=current_user,
            )

        self.assertEqual(payload["execution"]["adapter"], "alpaca_paper")
        self.assertEqual(payload["execution"]["broker_order_id"], "broker-order-1")
        self.assertEqual(payload["execution"]["broker_status"], "new")
        self.assertEqual(payload["pending_order"]["broker_order_id"], "broker-order-1")
        self.assertIn("capital_preservation", payload)

    def test_open_trade_respects_capital_preservation_position_lockout(self) -> None:
        from backend.core.database import Base
        from backend.schemas import OpenTradeRequest
        from backend.services import tenant_service, trade_service

        engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
        Session = sessionmaker(bind=engine, expire_on_commit=False, future=True)
        Base.metadata.create_all(bind=engine)

        fake_settings = SimpleNamespace(
            demo_tenant_slug="alpha-desk",
            demo_tenant_name="Alpha Desk",
            demo_tenant_plan="pro",
        )
        report = {
            "ticker": "SPY",
            "interval": "5m",
            "trade_decision": "VALID TRADE",
            "reject_reason": "",
            "event_risk": False,
            "option_plan": {
                "recommended_contract": None,
                "expected_underlying_target": 510.0,
                "invalidation_price": 496.0,
                "take_profit_1": 0.2,
                "take_profit_2": 0.35,
            },
        }
        position = {
            "suggested_contracts": 1,
            "total_position_cost": 240.0,
            "max_risk_dollars": 5.0,
        }
        fake_adapter = MagicMock()
        fake_adapter.adapter_name = "desk"

        with (
            Session() as db,
            patch.object(tenant_service, "settings", fake_settings),
            patch.object(trade_service, "analyze_market", return_value={"report": report, "live_price": 24.0}),
            patch.object(trade_service, "_build_equity_position_preview", return_value=position),
            patch.object(trade_service.sdm, "read_open_trades", return_value=pd.DataFrame([{"ticker": "QQQ"}])),
            patch.object(trade_service.sdm, "read_pending_orders", return_value=pd.DataFrame()),
            patch.object(trade_service.sdm, "read_closed_trades", return_value=pd.DataFrame()),
            patch.object(trade_service, "get_execution_adapter", return_value=fake_adapter),
        ):
            identity = tenant_service.ensure_demo_tenant_for_user(
                db,
                auth_subject="demo-trader",
                email="demo@example.test",
                name="Demo Trader",
                provider="local-demo",
                requested_tenant_slug="alpha-desk",
            )
            current_user = SimpleNamespace(
                tenant_id=identity["active_tenant"].id,
                tenant_slug="alpha-desk",
                tenant_name="Alpha Desk",
                tenant_status="active",
                tenant_plan="pro",
                auth_subject="demo-trader",
                user_id="demo-trader",
            )

            with self.assertRaises(trade_service.ValidationServiceError) as ctx:
                trade_service.open_trade_from_request(
                    OpenTradeRequest(
                        ticker="SPY",
                        interval="5m",
                        horizon=5,
                        live_price=24.0,
                        account_size=1000.0,
                        risk_percent=0.5,
                        instrument_type="equity",
                        order_type="limit",
                        time_in_force="day",
                        limit_price=24.0,
                        capital_preservation_mode=True,
                        regular_hours_only=True,
                        equities_only=True,
                        limit_orders_only=True,
                        max_open_positions=1,
                    ),
                    db=db,
                    current_user=current_user,
                )

        self.assertIn("active ticket", str(ctx.exception).lower())
        fake_adapter.submit_order.assert_not_called()

    def test_open_trade_allows_fractional_equity_in_tiny_account_mode(self) -> None:
        from backend.core.database import Base
        from backend.schemas import OpenTradeRequest
        from backend.services import tenant_service, trade_service
        from backend.services.execution.types import SubmitOrderResult

        engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
        Session = sessionmaker(bind=engine, expire_on_commit=False, future=True)
        Base.metadata.create_all(bind=engine)

        fake_settings = SimpleNamespace(
            demo_tenant_slug="alpha-desk",
            demo_tenant_name="Alpha Desk",
            demo_tenant_plan="pro",
        )
        report = {
            "ticker": "SPY",
            "interval": "5m",
            "trade_decision": "VALID TRADE",
            "reject_reason": "",
            "event_risk": False,
            "option_plan": {
                "recommended_contract": None,
                "expected_underlying_target": 102.0,
                "invalidation_price": 90.0,
                "take_profit_1": 0.2,
                "take_profit_2": 0.35,
            },
        }
        position = {
            "suggested_contracts": 0.05,
            "total_position_cost": 5.0,
            "max_risk_dollars": 5.0,
            "fractional_shares_only": True,
        }
        broker_record = {
            "trade_id": "trade-tiny-1",
            "order_id": "order-tiny-1",
            "ticker": "SPY",
            "status": "PENDING",
            "broker_name": "alpaca_paper",
            "broker_order_id": "broker-tiny-1",
            "broker_status": "new",
            "suggested_contracts": 0.05,
        }
        fake_adapter = MagicMock()
        fake_adapter.adapter_name = "alpaca_paper"
        fake_adapter.submit_order.return_value = SubmitOrderResult(
            position_opened=False,
            record=broker_record,
            pending_order=broker_record,
            broker_name="alpaca_paper",
            broker_order_id="broker-tiny-1",
            broker_status="new",
            broker_response={"id": "broker-tiny-1", "status": "new"},
        )

        with (
            Session() as db,
            patch.object(tenant_service, "settings", fake_settings),
            patch.object(trade_service, "analyze_market", return_value={"report": report, "live_price": 100.0}),
            patch.object(trade_service, "_build_equity_position_preview", return_value=position),
            patch.object(trade_service.sdm, "read_open_trades", return_value=pd.DataFrame()),
            patch.object(trade_service.sdm, "read_pending_orders", return_value=pd.DataFrame()),
            patch.object(trade_service.sdm, "read_closed_trades", return_value=pd.DataFrame()),
            patch.object(trade_service, "get_execution_adapter", return_value=fake_adapter),
        ):
            identity = tenant_service.ensure_demo_tenant_for_user(
                db,
                auth_subject="demo-trader",
                email="demo@example.test",
                name="Demo Trader",
                provider="local-demo",
                requested_tenant_slug="alpha-desk",
            )
            current_user = SimpleNamespace(
                tenant_id=identity["active_tenant"].id,
                tenant_slug="alpha-desk",
                tenant_name="Alpha Desk",
                tenant_status="active",
                tenant_plan="pro",
                auth_subject="demo-trader",
                user_id="demo-trader",
            )

            payload = trade_service.open_trade_from_request(
                OpenTradeRequest(
                    ticker="SPY",
                    interval="5m",
                    horizon=5,
                    live_price=100.0,
                    account_size=10.0,
                    risk_percent=0.5,
                    instrument_type="equity",
                    order_type="limit",
                    time_in_force="day",
                    limit_price=100.0,
                    capital_preservation_mode=True,
                    tiny_account_mode=True,
                    fractional_shares_only=True,
                    regular_hours_only=True,
                    max_daily_loss_r=1.0,
                    max_consecutive_losses=1,
                    max_open_positions=1,
                    max_notional_per_trade=5.0,
                    equities_only=True,
                    limit_orders_only=True,
                    long_only=True,
                ),
                db=db,
                current_user=current_user,
            )

        self.assertTrue(payload["opened"])
        self.assertEqual(payload["execution"]["adapter"], "alpaca_paper")
        self.assertEqual(payload["position"]["suggested_contracts"], 0.05)
        fake_adapter.submit_order.assert_called_once()

    def test_trade_summary_reports_capital_preservation_metrics(self) -> None:
        from backend.services import trade_service

        now_utc = pd.Timestamp.now(tz="UTC")
        today = now_utc.isoformat()
        earlier_today = (now_utc - pd.Timedelta(hours=2)).isoformat()
        yesterday = (now_utc - pd.Timedelta(days=1)).isoformat()

        closed_trades = pd.DataFrame(
            [
                {"ticker": "SPY", "closed_at": yesterday, "realized_pnl": 40.0},
                {"ticker": "QQQ", "closed_at": earlier_today, "realized_pnl": -25.0},
                {"ticker": "AAPL", "closed_at": today, "realized_pnl": -30.0},
            ]
        )

        with (
            patch.object(trade_service.sdm, "read_open_trades", return_value=pd.DataFrame([{"ticker": "SPY"}])),
            patch.object(trade_service.sdm, "read_pending_orders", return_value=pd.DataFrame([{"ticker": "QQQ"}])),
            patch.object(trade_service.sdm, "read_closed_trades", return_value=closed_trades),
            patch.object(trade_service.sdm, "monitor_open_trades", return_value=pd.DataFrame()),
            patch.object(trade_service, "get_pending_orders_snapshot", return_value={"items": [], "count": 0, "order_events": {"items": [], "count": 0, "status_counts": {}}}),
            patch.object(trade_service, "get_order_events_snapshot", return_value={"items": [], "count": 0, "status_counts": {}}),
        ):
            payload = trade_service.get_trade_summary()

        self.assertEqual(payload["capital_preservation"]["open_position_count"], 1)
        self.assertEqual(payload["capital_preservation"]["pending_order_count"], 1)
        self.assertEqual(payload["capital_preservation"]["active_ticket_count"], 2)
        self.assertEqual(payload["capital_preservation"]["consecutive_losses"], 2)
        self.assertEqual(payload["capital_preservation"]["today_realized_pnl"], -55.0)
        self.assertEqual(payload["attribution_summary"]["total_reviewed"], 3)
        self.assertEqual(payload["attribution_summary"]["thesis_review_count"], 2)
        self.assertEqual(payload["attribution_summary"]["clean_win_count"], 1)
        self.assertIn("trade_summary", payload)

    def test_open_trade_blocks_broker_live_until_rollout_ready(self) -> None:
        from backend.schemas import OpenTradeRequest
        from backend.services import portfolio_service, trade_service

        validation_snapshot = {
            "scorecards": [],
            "route_quality": {
                "clean_fill_count": 0,
                "slipped_fill_count": 1,
                "fragile_fill_count": 1,
                "rejected_route_count": 0,
                "partial_fill_count": 0,
                "average_abs_slippage_bps": 18.5,
                "latest_execution_review": None,
            },
            "board_snapshot_history": {"count": 1, "items": []},
            "replay_comparisons": {
                "board_outcomes": {
                    "count": 1,
                    "resolved_count": 1,
                    "open_count": 0,
                    "items": [{"status": "resolved", "pnl_dollars": -25.0}],
                },
                "paper_live_slippage": {
                    "count": 1,
                    "average_signed_slippage_bps": 18.5,
                    "average_abs_slippage_bps": 18.5,
                    "worst_abs_slippage_bps": 18.5,
                    "items": [],
                },
            },
        }
        lifecycle_health = {
            "summary": {
                "status": "healthy",
                "message": "Order lifecycle is healthy for the current pilot snapshot.",
                "pending_order_count": 0,
                "stale_pending_count": 0,
                "reject_count": 0,
                "fill_count": 0,
                "closed_count": 0,
                "last_event_at": None,
                "last_reject_at": None,
                "last_fill_at": None,
            },
            "checks": [],
            "stale_pending_orders": [],
            "recent_rejections": [],
            "recent_fills": [],
            "recent_closed": [],
        }

        with (
            patch.object(trade_service.sdm, "read_open_trades", return_value=pd.DataFrame()),
            patch.object(trade_service.sdm, "read_pending_orders", return_value=pd.DataFrame()),
            patch.object(trade_service.sdm, "read_closed_trades", return_value=pd.DataFrame()),
            patch.object(portfolio_service, "_build_validation_snapshot", return_value=validation_snapshot),
            patch.object(trade_service, "get_order_lifecycle_health_snapshot", return_value=lifecycle_health),
        ):
            with self.assertRaises(trade_service.ValidationServiceError) as ctx:
                trade_service.open_trade_from_request(
                    OpenTradeRequest(
                        ticker="SPY",
                        interval="5m",
                        horizon=5,
                        live_price=500.0,
                        account_size=10000.0,
                        risk_percent=1.0,
                        instrument_type="equity",
                        order_type="limit",
                        execution_intent="broker_live",
                    )
                )

        self.assertIn("Broker-live routing is still locked", str(ctx.exception))

    def test_open_trade_records_live_pilot_audit_when_broker_live_routes(self) -> None:
        from backend.core.database import Base
        from backend.models.saas import OrderEventRecord
        from backend.schemas import OpenTradeRequest
        from backend.services import portfolio_service, tenant_service, trade_service
        from backend.services.execution.types import SubmitOrderResult

        engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
        Session = sessionmaker(bind=engine, expire_on_commit=False, future=True)
        Base.metadata.create_all(bind=engine)

        fake_settings = SimpleNamespace(
            demo_tenant_slug="alpha-desk",
            demo_tenant_name="Alpha Desk",
            demo_tenant_plan="pro",
        )
        validation_snapshot = {
            "scorecards": [],
            "route_quality": {
                "clean_fill_count": 4,
                "slipped_fill_count": 0,
                "fragile_fill_count": 0,
                "rejected_route_count": 0,
                "partial_fill_count": 0,
                "average_abs_slippage_bps": 8.4,
                "latest_execution_review": None,
            },
            "board_snapshot_history": {
                "count": 4,
                "items": [
                    {"updated_at": "2026-04-10T15:30:00Z", "leader_ticker": "SPY", "board_name": "Controlled liquid board"},
                    {"updated_at": "2026-04-11T15:30:00Z", "leader_ticker": "QQQ", "board_name": "Controlled liquid board"},
                    {"updated_at": "2026-04-12T15:30:00Z", "leader_ticker": "NVDA", "board_name": "Controlled liquid board"},
                    {"updated_at": "2026-04-13T15:30:00Z", "leader_ticker": "AAPL", "board_name": "Controlled liquid board"},
                ],
            },
            "replay_comparisons": {
                "board_outcomes": {
                    "count": 4,
                    "resolved_count": 4,
                    "open_count": 0,
                    "items": [
                        {"status": "resolved", "pnl_dollars": 120.0, "resolved_at": "2026-04-10T16:00:00Z"},
                        {"status": "resolved", "pnl_dollars": 45.0, "resolved_at": "2026-04-11T16:00:00Z"},
                        {"status": "resolved", "pnl_dollars": -15.0, "resolved_at": "2026-04-12T16:00:00Z"},
                        {"status": "resolved", "pnl_dollars": 30.0, "resolved_at": "2026-04-13T16:00:00Z"},
                    ],
                },
                "paper_live_slippage": {
                    "count": 4,
                    "average_signed_slippage_bps": 1.2,
                    "average_abs_slippage_bps": 8.4,
                    "worst_abs_slippage_bps": 18.0,
                    "items": [
                        {"closed_at": "2026-04-10T16:00:00Z", "slippage_bps": 14.0},
                        {"closed_at": "2026-04-11T16:00:00Z", "slippage_bps": 11.0},
                        {"closed_at": "2026-04-12T16:00:00Z", "slippage_bps": 9.0},
                        {"closed_at": "2026-04-13T16:00:00Z", "slippage_bps": 8.4},
                    ],
                },
            },
            "ranked_entry_rollout": {
                "available": True,
                "accepted": True,
                "status": "accepted",
                "basis": "Ranked-entry validation accepts profile M for live-gate review.",
                "baseline_key": "A",
                "candidate_key": "M",
                "baseline": {"ending_equity": 102000.0, "average_trade_profit": 120.0, "max_drawdown_pct": 10.0},
                "candidate": {"ending_equity": 103500.0, "average_trade_profit": 135.0, "max_drawdown_pct": 11.0, "gross_exposure_peak": 145000.0},
                "current_route_fill_count": 10,
                "current_route_closed_trade_count": 5,
                "metrics_source": "mark_to_market",
                "mark_to_market_coverage_status": "complete",
                "ledger_snapshot_consistency": "consistent",
                "current_route_sample_status": "sufficient",
                "route_window_start": "2026-04-10T14:35:00+00:00",
                "route_window_end": "2026-04-13T16:00:00+00:00",
                "route_window_snapshot_count": 12,
                "all_history_validation_integrity": {
                    "metrics_source": "event_ledger",
                    "mark_to_market_coverage_status": "partial_window",
                    "ledger_snapshot_consistency": "unavailable",
                },
                "current_route_validation_integrity": {
                    "metrics_source": "mark_to_market",
                    "mark_to_market_coverage_status": "complete",
                    "ledger_snapshot_consistency": "consistent",
                    "current_route_sample_status": "sufficient",
                    "route_window_start": "2026-04-10T14:35:00+00:00",
                    "route_window_end": "2026-04-13T16:00:00+00:00",
                    "route_window_snapshot_count": 12,
                },
            },
        }
        lifecycle_health = {
            "summary": {
                "status": "healthy",
                "message": "Order lifecycle is healthy for the current pilot snapshot.",
                "pending_order_count": 0,
                "stale_pending_count": 0,
                "reject_count": 0,
                "fill_count": 0,
                "closed_count": 0,
                "last_event_at": None,
                "last_reject_at": None,
                "last_fill_at": None,
            },
            "checks": [],
            "stale_pending_orders": [],
            "recent_rejections": [],
            "recent_fills": [],
            "recent_closed": [],
        }
        report = {
            "ticker": "SPY",
            "interval": "5m",
            "trade_decision": "VALID TRADE",
            "reject_reason": "",
            "event_risk": False,
            "option_plan": {
                "recommended_contract": None,
                "expected_underlying_target": 510.0,
                "invalidation_price": 496.0,
                "take_profit_1": 0.2,
                "take_profit_2": 0.35,
            },
        }
        position = {
            "suggested_contracts": 2,
            "total_position_cost": 1001.0,
            "max_risk_dollars": 100.0,
        }
        broker_record = {
            "trade_id": "trade-live-pilot-1",
            "order_id": "internal-live-pilot-1",
            "broker_name": "alpaca_live",
            "broker_order_id": "broker-live-1",
            "broker_status": "new",
            "ticker": "SPY",
            "status": "PENDING",
        }
        fake_adapter = MagicMock()
        fake_adapter.adapter_name = "alpaca_live"
        fake_adapter.submit_order.return_value = SubmitOrderResult(
            position_opened=False,
            record=broker_record,
            pending_order=broker_record,
            broker_name="alpaca_live",
            broker_order_id="broker-live-1",
            broker_status="new",
            broker_response={"id": "broker-live-1", "status": "new"},
        )

        with (
            Session() as db,
            patch.object(tenant_service, "settings", fake_settings),
            patch.object(trade_service, "analyze_market", return_value={"report": report, "live_price": 500.5}),
            patch.object(trade_service, "_build_equity_position_preview", return_value=position),
            patch.object(trade_service.sdm, "read_open_trades", return_value=pd.DataFrame()),
            patch.object(trade_service.sdm, "read_pending_orders", return_value=pd.DataFrame()),
            patch.object(trade_service.sdm, "read_closed_trades", return_value=pd.DataFrame()),
            patch.object(portfolio_service, "_build_validation_snapshot", return_value=validation_snapshot),
            patch.object(trade_service, "get_order_lifecycle_health_snapshot", return_value=lifecycle_health),
            patch.object(trade_service, "get_execution_adapter_for", return_value=fake_adapter),
            patch.object(trade_service, "get_pending_orders_snapshot", return_value={"items": [], "count": 0, "order_events": {"items": [], "count": 0, "status_counts": {}}}),
        ):
            identity = tenant_service.ensure_demo_tenant_for_user(
                db,
                auth_subject="demo-trader",
                email="demo@example.test",
                name="Demo Trader",
                provider="local-demo",
                requested_tenant_slug="alpha-desk",
            )
            current_user = SimpleNamespace(
                tenant_id=identity["active_tenant"].id,
                tenant_slug="alpha-desk",
                tenant_name="Alpha Desk",
                tenant_status="active",
                tenant_plan="pro",
                auth_subject="demo-trader",
                user_id="demo-trader",
            )

            payload = trade_service.open_trade_from_request(
                OpenTradeRequest(
                    ticker="SPY",
                    interval="5m",
                    horizon=5,
                    live_price=500.5,
                    account_size=10000,
                    risk_percent=0.5,
                    instrument_type="equity",
                    order_type="limit",
                    time_in_force="day",
                    limit_price=500.5,
                    execution_intent="broker_live",
                ),
                db=db,
                current_user=current_user,
            )
            stored_events = db.execute(select(OrderEventRecord).order_by(OrderEventRecord.created_at.asc())).scalars().all()

        accepted_payload = stored_events[-1].payload_json or {}
        self.assertTrue(payload["opened"])
        self.assertEqual(payload["execution"]["intent"], "broker_live")
        self.assertEqual(payload["live_pilot_audit"]["count"], 1)
        self.assertEqual(payload["live_pilot_audit"]["latest"]["gate_label"], "Pilot live ready")
        self.assertEqual(accepted_payload["execution"]["adapter"], "alpaca_live")
        self.assertEqual(accepted_payload["rollout_audit"]["execution_intent"], "broker_live")
        self.assertEqual(accepted_payload["rollout_audit"]["gate_label"], "Pilot live ready")
        self.assertTrue(accepted_payload["rollout_audit"]["allows_live_rollout"])

    def test_trade_summary_reports_rollout_readiness(self) -> None:
        from backend.services import portfolio_service, trade_service

        validation_snapshot = {
            "scorecards": [],
            "route_quality": {
                "clean_fill_count": 4,
                "slipped_fill_count": 0,
                "fragile_fill_count": 0,
                "rejected_route_count": 0,
                "partial_fill_count": 0,
                "average_abs_slippage_bps": 8.4,
                "latest_execution_review": None,
            },
            "board_snapshot_history": {
                "count": 4,
                "items": [
                    {"updated_at": "2026-04-10T15:30:00Z", "leader_ticker": "SPY", "board_name": "Controlled liquid board"},
                    {"updated_at": "2026-04-11T15:30:00Z", "leader_ticker": "QQQ", "board_name": "Controlled liquid board"},
                    {"updated_at": "2026-04-12T15:30:00Z", "leader_ticker": "NVDA", "board_name": "Controlled liquid board"},
                    {"updated_at": "2026-04-13T15:30:00Z", "leader_ticker": "AAPL", "board_name": "Controlled liquid board"},
                ],
            },
            "replay_comparisons": {
                "board_outcomes": {
                    "count": 4,
                    "resolved_count": 4,
                    "open_count": 0,
                    "items": [
                        {"status": "resolved", "pnl_dollars": 120.0, "resolved_at": "2026-04-10T16:00:00Z"},
                        {"status": "resolved", "pnl_dollars": 45.0, "resolved_at": "2026-04-11T16:00:00Z"},
                        {"status": "resolved", "pnl_dollars": -15.0, "resolved_at": "2026-04-12T16:00:00Z"},
                        {"status": "resolved", "pnl_dollars": 30.0, "resolved_at": "2026-04-13T16:00:00Z"},
                    ],
                },
                "paper_live_slippage": {
                    "count": 4,
                    "average_signed_slippage_bps": 1.2,
                    "average_abs_slippage_bps": 8.4,
                    "worst_abs_slippage_bps": 18.0,
                    "items": [
                        {"closed_at": "2026-04-10T16:00:00Z", "slippage_bps": 14.0},
                        {"closed_at": "2026-04-11T16:00:00Z", "slippage_bps": 11.0},
                        {"closed_at": "2026-04-12T16:00:00Z", "slippage_bps": 9.0},
                        {"closed_at": "2026-04-13T16:00:00Z", "slippage_bps": 8.4},
                    ],
                },
            },
            "ranked_entry_rollout": {
                "available": True,
                "accepted": True,
                "status": "accepted",
                "basis": "Ranked-entry validation accepts profile M for live-gate review.",
                "baseline_key": "A",
                "candidate_key": "M",
                "baseline": {"ending_equity": 102000.0, "average_trade_profit": 120.0, "max_drawdown_pct": 10.0},
                "candidate": {"ending_equity": 103500.0, "average_trade_profit": 135.0, "max_drawdown_pct": 11.0, "gross_exposure_peak": 145000.0},
                "current_route_fill_count": 10,
                "current_route_closed_trade_count": 5,
                "metrics_source": "mark_to_market",
                "mark_to_market_coverage_status": "complete",
                "ledger_snapshot_consistency": "consistent",
                "current_route_sample_status": "sufficient",
                "route_window_start": "2026-04-10T14:35:00+00:00",
                "route_window_end": "2026-04-13T16:00:00+00:00",
                "route_window_snapshot_count": 12,
                "all_history_validation_integrity": {
                    "metrics_source": "event_ledger",
                    "mark_to_market_coverage_status": "partial_window",
                    "ledger_snapshot_consistency": "unavailable",
                },
                "current_route_validation_integrity": {
                    "metrics_source": "mark_to_market",
                    "mark_to_market_coverage_status": "complete",
                    "ledger_snapshot_consistency": "consistent",
                    "current_route_sample_status": "sufficient",
                    "route_window_start": "2026-04-10T14:35:00+00:00",
                    "route_window_end": "2026-04-13T16:00:00+00:00",
                    "route_window_snapshot_count": 12,
                },
            },
        }
        lifecycle_health = {
            "summary": {
                "status": "healthy",
                "message": "Order lifecycle is healthy for the current pilot snapshot.",
                "pending_order_count": 0,
                "stale_pending_count": 0,
                "reject_count": 0,
                "fill_count": 3,
                "closed_count": 2,
                "last_event_at": None,
                "last_reject_at": None,
                "last_fill_at": None,
            },
            "checks": [],
            "stale_pending_orders": [],
            "recent_rejections": [],
            "recent_fills": [],
            "recent_closed": [],
        }

        with (
            patch.object(trade_service.sdm, "read_open_trades", return_value=pd.DataFrame()),
            patch.object(trade_service.sdm, "read_pending_orders", return_value=pd.DataFrame()),
            patch.object(trade_service.sdm, "read_closed_trades", return_value=pd.DataFrame()),
            patch.object(trade_service.sdm, "monitor_open_trades", return_value=pd.DataFrame()),
            patch.object(trade_service.sdm, "trade_summary", return_value={"total_realized_pnl": 0}),
            patch.object(trade_service, "get_pending_orders_snapshot", return_value={"items": [], "count": 0, "order_events": {"items": [], "count": 0, "status_counts": {}}}),
            patch.object(trade_service, "get_order_events_snapshot", return_value={"items": [], "count": 0, "status_counts": {}}),
            patch.object(trade_service, "get_order_lifecycle_health_snapshot", return_value=lifecycle_health),
            patch.object(portfolio_service, "_build_validation_snapshot", return_value=validation_snapshot),
        ):
            payload = trade_service.get_trade_summary()

        self.assertEqual(payload["rollout_readiness"]["status"], "ready")
        self.assertTrue(payload["rollout_readiness"]["allows_live_rollout"])
        self.assertEqual(payload["rollout_readiness"]["metrics"]["resolved_count"], 4)
        self.assertAlmostEqual(payload["rollout_readiness"]["metrics"]["replay_win_rate"], 0.75)
        self.assertTrue(payload["rollout_readiness"]["metrics"]["ranked_entry_accepted"])
        self.assertEqual(payload["rollout_readiness"]["metrics"]["candidate_profile_key"], "M")
        self.assertEqual(payload["rollout_readiness"]["metrics"]["current_route_fill_count"], 10)
        self.assertEqual(payload["rollout_readiness"]["metrics"]["current_route_closed_trade_count"], 5)
        self.assertEqual(payload["rollout_readiness"]["metrics"]["metrics_source"], "mark_to_market")
        self.assertEqual(payload["rollout_readiness"]["metrics"]["mark_to_market_coverage_status"], "complete")
        self.assertEqual(payload["rollout_readiness"]["metrics"]["ledger_snapshot_consistency"], "consistent")
        self.assertEqual(payload["rollout_readiness"]["metrics"]["current_route_sample_status"], "sufficient")
        self.assertEqual(payload["rollout_readiness"]["metrics"]["route_window_start"], "2026-04-10T14:35:00+00:00")
        self.assertEqual(payload["rollout_readiness"]["metrics"]["route_window_end"], "2026-04-13T16:00:00+00:00")
        self.assertEqual(payload["rollout_readiness"]["metrics"]["route_window_snapshot_count"], 12)
        self.assertEqual(payload["rollout_readiness"]["metrics"]["audit_metrics_source"], "event_ledger")
        self.assertEqual(payload["rollout_readiness"]["metrics"]["audit_mark_to_market_coverage_status"], "partial_window")
        self.assertEqual(payload["rollout_readiness"]["metrics"]["audit_ledger_snapshot_consistency"], "unavailable")
        self.assertEqual(payload["rollout_readiness"]["checks"][-2]["key"], "validation_sample")
        self.assertEqual(payload["rollout_readiness"]["checks"][-2]["tone"], "positive")
        self.assertEqual(payload["validation_snapshot"]["replay_comparisons"]["paper_live_slippage"]["count"], 4)
        self.assertEqual(payload["rollout_readiness"]["history"]["trend"], "improving")
        self.assertGreaterEqual(payload["rollout_readiness"]["history"]["count"], 2)
        self.assertEqual(payload["rollout_readiness"]["history"]["items"][-1]["label"], "Pilot live ready")
        self.assertEqual(payload["rollout_readiness"]["ranked_entry_rollout"]["status"], "accepted")
        self.assertEqual(payload["rollout_readiness"]["all_history_validation_integrity"]["metrics_source"], "event_ledger")
        self.assertEqual(payload["rollout_readiness"]["current_route_validation_integrity"]["metrics_source"], "mark_to_market")

    def test_trade_summary_blocks_live_rollout_when_ranked_entry_profile_is_rejected(self) -> None:
        from backend.services import portfolio_service, trade_service

        validation_snapshot = {
            "scorecards": [],
            "route_quality": {
                "clean_fill_count": 4,
                "slipped_fill_count": 0,
                "fragile_fill_count": 0,
                "rejected_route_count": 0,
                "partial_fill_count": 0,
                "average_abs_slippage_bps": 8.4,
                "latest_execution_review": None,
            },
            "board_snapshot_history": {"count": 0, "items": []},
            "replay_comparisons": {
                "board_outcomes": {
                    "count": 4,
                    "resolved_count": 4,
                    "open_count": 0,
                    "items": [
                        {"status": "resolved", "pnl_dollars": 120.0},
                        {"status": "resolved", "pnl_dollars": 45.0},
                        {"status": "resolved", "pnl_dollars": -15.0},
                        {"status": "resolved", "pnl_dollars": 30.0},
                    ],
                },
                "paper_live_slippage": {
                    "count": 4,
                    "average_signed_slippage_bps": 1.2,
                    "average_abs_slippage_bps": 8.4,
                    "worst_abs_slippage_bps": 18.0,
                    "items": [],
                },
            },
            "ranked_entry_rollout": {
                "available": True,
                "accepted": False,
                "status": "rejected",
                "basis": "Candidate drawdown 12.00% exceeds the allowed 11.50% ceiling.",
                "failure_basis": "Candidate drawdown 12.00% exceeds the allowed 11.50% ceiling.",
                "baseline_key": "A",
                "candidate_key": "M",
                "baseline": {"ending_equity": 102000.0, "average_trade_profit": 120.0, "max_drawdown_pct": 10.0},
                "candidate": {"ending_equity": 103000.0, "average_trade_profit": 140.0, "max_drawdown_pct": 12.0, "gross_exposure_peak": 140000.0},
                "current_route_fill_count": 12,
                "current_route_closed_trade_count": 6,
                "current_route_validation_integrity": {
                    "metrics_source": "mark_to_market",
                    "mark_to_market_coverage_status": "complete",
                    "ledger_snapshot_consistency": "consistent",
                    "current_route_sample_status": "sufficient",
                    "route_window_start": "2026-04-10T14:35:00+00:00",
                    "route_window_end": "2026-04-13T16:00:00+00:00",
                    "route_window_snapshot_count": 12,
                },
            },
        }
        lifecycle_health = {
            "summary": {
                "status": "healthy",
                "message": "Order lifecycle is healthy for the current pilot snapshot.",
                "pending_order_count": 0,
                "stale_pending_count": 0,
                "reject_count": 0,
                "fill_count": 3,
                "closed_count": 2,
                "last_event_at": None,
                "last_reject_at": None,
                "last_fill_at": None,
            },
            "checks": [],
            "stale_pending_orders": [],
            "recent_rejections": [],
            "recent_fills": [],
            "recent_closed": [],
        }

        with (
            patch.object(trade_service.sdm, "read_open_trades", return_value=pd.DataFrame()),
            patch.object(trade_service.sdm, "read_pending_orders", return_value=pd.DataFrame()),
            patch.object(trade_service.sdm, "read_closed_trades", return_value=pd.DataFrame()),
            patch.object(trade_service.sdm, "monitor_open_trades", return_value=pd.DataFrame()),
            patch.object(trade_service.sdm, "trade_summary", return_value={"total_realized_pnl": 0}),
            patch.object(trade_service, "get_pending_orders_snapshot", return_value={"items": [], "count": 0, "order_events": {"items": [], "count": 0, "status_counts": {}}}),
            patch.object(trade_service, "get_order_events_snapshot", return_value={"items": [], "count": 0, "status_counts": {}}),
            patch.object(trade_service, "get_order_lifecycle_health_snapshot", return_value=lifecycle_health),
            patch.object(portfolio_service, "_build_validation_snapshot", return_value=validation_snapshot),
        ):
            payload = trade_service.get_trade_summary()

        self.assertEqual(payload["rollout_readiness"]["status"], "locked")
        self.assertEqual(payload["rollout_readiness"]["label"], "Validation only")
        self.assertFalse(payload["rollout_readiness"]["allows_live_rollout"])
        self.assertEqual(payload["rollout_readiness"]["checks"][-1]["key"], "ranked_entry_rollout")
        self.assertIn("drawdown", payload["rollout_readiness"]["basis"].lower())
        self.assertEqual(payload["rollout_readiness"]["ranked_entry_rollout"]["candidate_key"], "M")

    def test_trade_summary_blocks_live_rollout_when_validation_sample_is_incomplete(self) -> None:
        from backend.services import portfolio_service, trade_service

        validation_snapshot = {
            "scorecards": [],
            "route_quality": {
                "clean_fill_count": 4,
                "slipped_fill_count": 0,
                "fragile_fill_count": 0,
                "rejected_route_count": 0,
                "partial_fill_count": 0,
                "average_abs_slippage_bps": 8.4,
                "latest_execution_review": None,
            },
            "board_snapshot_history": {"count": 0, "items": []},
            "replay_comparisons": {
                "board_outcomes": {
                    "count": 4,
                    "resolved_count": 4,
                    "open_count": 0,
                    "items": [
                        {"status": "resolved", "pnl_dollars": 120.0},
                        {"status": "resolved", "pnl_dollars": 45.0},
                        {"status": "resolved", "pnl_dollars": -15.0},
                        {"status": "resolved", "pnl_dollars": 30.0},
                    ],
                },
                "paper_live_slippage": {
                    "count": 4,
                    "average_signed_slippage_bps": 1.2,
                    "average_abs_slippage_bps": 8.4,
                    "worst_abs_slippage_bps": 18.0,
                    "items": [],
                },
            },
            "ranked_entry_rollout": {
                "available": True,
                "accepted": True,
                "status": "accepted",
                "basis": "Ranked-entry validation accepts profile M for live-gate review.",
                "baseline_key": "A",
                "candidate_key": "M",
                "baseline": {"ending_equity": 102000.0, "average_trade_profit": 120.0, "max_drawdown_pct": 10.0},
                "candidate": {"ending_equity": 103500.0, "average_trade_profit": 135.0, "max_drawdown_pct": 11.0, "gross_exposure_peak": 145000.0},
                "current_route_fill_count": 6,
                "current_route_closed_trade_count": 2,
                "metrics_source": "event_ledger",
                "mark_to_market_coverage_status": "partial_window",
                "ledger_snapshot_consistency": "unavailable",
                "current_route_sample_status": "insufficient",
            },
        }
        lifecycle_health = {
            "summary": {
                "status": "healthy",
                "message": "Order lifecycle is healthy for the current pilot snapshot.",
                "pending_order_count": 0,
                "stale_pending_count": 0,
                "reject_count": 0,
                "fill_count": 6,
                "closed_count": 2,
                "last_event_at": None,
                "last_reject_at": None,
                "last_fill_at": None,
            },
            "checks": [],
            "stale_pending_orders": [],
            "recent_rejections": [],
            "recent_fills": [],
            "recent_closed": [],
        }

        with (
            patch.object(trade_service.sdm, "read_open_trades", return_value=pd.DataFrame()),
            patch.object(trade_service.sdm, "read_pending_orders", return_value=pd.DataFrame()),
            patch.object(trade_service.sdm, "read_closed_trades", return_value=pd.DataFrame()),
            patch.object(trade_service.sdm, "monitor_open_trades", return_value=pd.DataFrame()),
            patch.object(trade_service.sdm, "trade_summary", return_value={"total_realized_pnl": 0}),
            patch.object(trade_service, "get_pending_orders_snapshot", return_value={"items": [], "count": 0, "order_events": {"items": [], "count": 0, "status_counts": {}}}),
            patch.object(trade_service, "get_order_events_snapshot", return_value={"items": [], "count": 0, "status_counts": {}}),
            patch.object(trade_service, "get_order_lifecycle_health_snapshot", return_value=lifecycle_health),
            patch.object(portfolio_service, "_build_validation_snapshot", return_value=validation_snapshot),
        ):
            payload = trade_service.get_trade_summary()

        validation_sample_check = next(
            check for check in payload["rollout_readiness"]["checks"] if check["key"] == "validation_sample"
        )
        self.assertEqual(payload["rollout_readiness"]["status"], "locked")
        self.assertEqual(payload["rollout_readiness"]["label"], "Paper first")
        self.assertFalse(payload["rollout_readiness"]["allows_live_rollout"])
        self.assertEqual(payload["rollout_readiness"]["metrics"]["current_route_fill_count"], 6)
        self.assertEqual(payload["rollout_readiness"]["metrics"]["current_route_closed_trade_count"], 2)
        self.assertEqual(payload["rollout_readiness"]["metrics"]["mark_to_market_coverage_status"], "partial_window")
        self.assertEqual(payload["rollout_readiness"]["metrics"]["ledger_snapshot_consistency"], "unavailable")
        self.assertEqual(payload["rollout_readiness"]["metrics"]["current_route_sample_status"], "insufficient")
        self.assertEqual(validation_sample_check["tone"], "negative")
        self.assertIn("current-route", validation_sample_check["message"].lower())

    def test_trade_summary_blocks_live_rollout_when_current_route_snapshot_window_is_incomplete(self) -> None:
        from backend.services import portfolio_service, trade_service

        validation_snapshot = {
            "scorecards": [],
            "route_quality": {
                "clean_fill_count": 4,
                "slipped_fill_count": 0,
                "fragile_fill_count": 0,
                "rejected_route_count": 0,
                "partial_fill_count": 0,
                "average_abs_slippage_bps": 8.4,
                "latest_execution_review": None,
            },
            "board_snapshot_history": {"count": 0, "items": []},
            "replay_comparisons": {
                "board_outcomes": {
                    "count": 4,
                    "resolved_count": 4,
                    "open_count": 0,
                    "items": [
                        {"status": "resolved", "pnl_dollars": 120.0},
                        {"status": "resolved", "pnl_dollars": 45.0},
                        {"status": "resolved", "pnl_dollars": -15.0},
                        {"status": "resolved", "pnl_dollars": 30.0},
                    ],
                },
                "paper_live_slippage": {
                    "count": 4,
                    "average_signed_slippage_bps": 1.2,
                    "average_abs_slippage_bps": 8.4,
                    "worst_abs_slippage_bps": 18.0,
                    "items": [],
                },
            },
            "ranked_entry_rollout": {
                "available": True,
                "accepted": True,
                "status": "accepted",
                "basis": "Ranked-entry validation accepts profile M for live-gate review.",
                "baseline_key": "A",
                "candidate_key": "M",
                "baseline": {"ending_equity": 102000.0, "average_trade_profit": 120.0, "max_drawdown_pct": 10.0},
                "candidate": {"ending_equity": 103500.0, "average_trade_profit": 135.0, "max_drawdown_pct": 11.0, "gross_exposure_peak": 145000.0},
                "current_route_fill_count": 12,
                "current_route_closed_trade_count": 6,
                "current_route_validation_integrity": {
                    "metrics_source": "event_ledger",
                    "mark_to_market_coverage_status": "partial_window",
                    "ledger_snapshot_consistency": "unavailable",
                    "current_route_sample_status": "sufficient",
                    "route_window_start": "2026-04-10T14:35:00+00:00",
                    "route_window_end": "2026-04-13T16:00:00+00:00",
                    "route_window_snapshot_count": 1,
                },
            },
        }
        lifecycle_health = {
            "summary": {
                "status": "healthy",
                "message": "Order lifecycle is healthy for the current pilot snapshot.",
                "pending_order_count": 0,
                "stale_pending_count": 0,
                "reject_count": 0,
                "fill_count": 12,
                "closed_count": 6,
                "last_event_at": None,
                "last_reject_at": None,
                "last_fill_at": None,
            },
            "checks": [],
            "stale_pending_orders": [],
            "recent_rejections": [],
            "recent_fills": [],
            "recent_closed": [],
        }

        with (
            patch.object(trade_service.sdm, "read_open_trades", return_value=pd.DataFrame()),
            patch.object(trade_service.sdm, "read_pending_orders", return_value=pd.DataFrame()),
            patch.object(trade_service.sdm, "read_closed_trades", return_value=pd.DataFrame()),
            patch.object(trade_service.sdm, "monitor_open_trades", return_value=pd.DataFrame()),
            patch.object(trade_service.sdm, "trade_summary", return_value={"total_realized_pnl": 0}),
            patch.object(trade_service, "get_pending_orders_snapshot", return_value={"items": [], "count": 0, "order_events": {"items": [], "count": 0, "status_counts": {}}}),
            patch.object(trade_service, "get_order_events_snapshot", return_value={"items": [], "count": 0, "status_counts": {}}),
            patch.object(trade_service, "get_order_lifecycle_health_snapshot", return_value=lifecycle_health),
            patch.object(portfolio_service, "_build_validation_snapshot", return_value=validation_snapshot),
        ):
            payload = trade_service.get_trade_summary()

        validation_sample_check = next(
            check for check in payload["rollout_readiness"]["checks"] if check["key"] == "validation_sample"
        )
        self.assertEqual(payload["rollout_readiness"]["status"], "locked")
        self.assertEqual(payload["rollout_readiness"]["label"], "Paper first")
        self.assertFalse(payload["rollout_readiness"]["allows_live_rollout"])
        self.assertEqual(payload["rollout_readiness"]["metrics"]["current_route_sample_status"], "sufficient")
        self.assertEqual(payload["rollout_readiness"]["metrics"]["mark_to_market_coverage_status"], "partial_window")
        self.assertEqual(payload["rollout_readiness"]["metrics"]["route_window_snapshot_count"], 1)
        self.assertEqual(validation_sample_check["tone"], "negative")
        self.assertIn("snapshot coverage", validation_sample_check["message"].lower())

    def test_trade_summary_blocks_live_rollout_when_current_route_accounting_is_inconsistent(self) -> None:
        from backend.services import portfolio_service, trade_service

        validation_snapshot = {
            "scorecards": [],
            "route_quality": {
                "clean_fill_count": 4,
                "slipped_fill_count": 0,
                "fragile_fill_count": 0,
                "rejected_route_count": 0,
                "partial_fill_count": 0,
                "average_abs_slippage_bps": 8.4,
                "latest_execution_review": None,
            },
            "board_snapshot_history": {"count": 0, "items": []},
            "replay_comparisons": {
                "board_outcomes": {
                    "count": 4,
                    "resolved_count": 4,
                    "open_count": 0,
                    "items": [
                        {"status": "resolved", "pnl_dollars": 120.0},
                        {"status": "resolved", "pnl_dollars": 45.0},
                        {"status": "resolved", "pnl_dollars": -15.0},
                        {"status": "resolved", "pnl_dollars": 30.0},
                    ],
                },
                "paper_live_slippage": {
                    "count": 4,
                    "average_signed_slippage_bps": 1.2,
                    "average_abs_slippage_bps": 8.4,
                    "worst_abs_slippage_bps": 18.0,
                    "items": [],
                },
            },
            "ranked_entry_rollout": {
                "available": True,
                "accepted": True,
                "status": "accepted",
                "basis": "Ranked-entry validation accepts profile M for live-gate review.",
                "baseline_key": "A",
                "candidate_key": "M",
                "baseline": {"ending_equity": 102000.0, "average_trade_profit": 120.0, "max_drawdown_pct": 10.0},
                "candidate": {"ending_equity": 103500.0, "average_trade_profit": 135.0, "max_drawdown_pct": 11.0, "gross_exposure_peak": 145000.0},
                "current_route_fill_count": 12,
                "current_route_closed_trade_count": 6,
                "current_route_validation_integrity": {
                    "metrics_source": "event_ledger",
                    "mark_to_market_coverage_status": "complete",
                    "ledger_snapshot_consistency": "inconsistent",
                    "current_route_sample_status": "sufficient",
                    "route_window_start": "2026-04-10T14:35:00+00:00",
                    "route_window_end": "2026-04-13T16:00:00+00:00",
                    "route_window_snapshot_count": 6,
                },
            },
        }
        lifecycle_health = {
            "summary": {
                "status": "healthy",
                "message": "Order lifecycle is healthy for the current pilot snapshot.",
                "pending_order_count": 0,
                "stale_pending_count": 0,
                "reject_count": 0,
                "fill_count": 12,
                "closed_count": 6,
                "last_event_at": None,
                "last_reject_at": None,
                "last_fill_at": None,
            },
            "checks": [],
            "stale_pending_orders": [],
            "recent_rejections": [],
            "recent_fills": [],
            "recent_closed": [],
        }

        with (
            patch.object(trade_service.sdm, "read_open_trades", return_value=pd.DataFrame()),
            patch.object(trade_service.sdm, "read_pending_orders", return_value=pd.DataFrame()),
            patch.object(trade_service.sdm, "read_closed_trades", return_value=pd.DataFrame()),
            patch.object(trade_service.sdm, "monitor_open_trades", return_value=pd.DataFrame()),
            patch.object(trade_service.sdm, "trade_summary", return_value={"total_realized_pnl": 0}),
            patch.object(trade_service, "get_pending_orders_snapshot", return_value={"items": [], "count": 0, "order_events": {"items": [], "count": 0, "status_counts": {}}}),
            patch.object(trade_service, "get_order_events_snapshot", return_value={"items": [], "count": 0, "status_counts": {}}),
            patch.object(trade_service, "get_order_lifecycle_health_snapshot", return_value=lifecycle_health),
            patch.object(portfolio_service, "_build_validation_snapshot", return_value=validation_snapshot),
        ):
            payload = trade_service.get_trade_summary()

        validation_sample_check = next(
            check for check in payload["rollout_readiness"]["checks"] if check["key"] == "validation_sample"
        )
        self.assertEqual(payload["rollout_readiness"]["status"], "locked")
        self.assertEqual(payload["rollout_readiness"]["label"], "Paper first")
        self.assertFalse(payload["rollout_readiness"]["allows_live_rollout"])
        self.assertEqual(payload["rollout_readiness"]["metrics"]["current_route_sample_status"], "sufficient")
        self.assertEqual(payload["rollout_readiness"]["metrics"]["mark_to_market_coverage_status"], "complete")
        self.assertEqual(payload["rollout_readiness"]["metrics"]["ledger_snapshot_consistency"], "inconsistent")
        self.assertEqual(validation_sample_check["tone"], "negative")
        self.assertIn("consistent", validation_sample_check["message"].lower())

    def test_trade_summary_blocks_live_rollout_when_current_route_reconciliation_has_orphans(self) -> None:
        from backend.services import portfolio_service, trade_service

        validation_snapshot = {
            "scorecards": [],
            "route_quality": {
                "clean_fill_count": 4,
                "slipped_fill_count": 0,
                "fragile_fill_count": 0,
                "rejected_route_count": 0,
                "partial_fill_count": 0,
                "average_abs_slippage_bps": 8.4,
                "latest_execution_review": None,
            },
            "board_snapshot_history": {"count": 0, "items": []},
            "replay_comparisons": {
                "board_outcomes": {
                    "count": 4,
                    "resolved_count": 4,
                    "open_count": 0,
                    "items": [
                        {"status": "resolved", "pnl_dollars": 120.0},
                        {"status": "resolved", "pnl_dollars": 45.0},
                        {"status": "resolved", "pnl_dollars": -15.0},
                        {"status": "resolved", "pnl_dollars": 30.0},
                    ],
                },
                "paper_live_slippage": {
                    "count": 4,
                    "average_signed_slippage_bps": 1.2,
                    "average_abs_slippage_bps": 8.4,
                    "worst_abs_slippage_bps": 18.0,
                    "items": [],
                },
            },
            "ranked_entry_rollout": {
                "available": True,
                "accepted": True,
                "status": "accepted",
                "basis": "Ranked-entry validation accepts profile M for live-gate review.",
                "baseline_key": "A",
                "candidate_key": "M",
                "current_route_fill_count": 12,
                "current_route_closed_trade_count": 6,
                "current_route_reconciliation_status": "orphaned",
                "current_route_orphan_order_event_count": 2,
                "last_submitted_current_route_order_at": "2026-04-10T14:35:00+00:00",
                "last_current_route_fill_at": "2026-04-10T14:36:00+00:00",
                "last_current_route_close_at": "2026-04-13T16:00:00+00:00",
                "current_route_validation_integrity": {
                    "metrics_source": "mark_to_market",
                    "mark_to_market_coverage_status": "complete",
                    "ledger_snapshot_consistency": "consistent",
                    "current_route_sample_status": "sufficient",
                    "route_window_start": "2026-04-10T14:35:00+00:00",
                    "route_window_end": "2026-04-13T16:00:00+00:00",
                    "route_window_snapshot_count": 12,
                },
            },
        }
        lifecycle_health = {
            "summary": {
                "status": "healthy",
                "message": "Order lifecycle is healthy for the current pilot snapshot.",
                "pending_order_count": 0,
                "stale_pending_count": 0,
                "reject_count": 0,
                "fill_count": 12,
                "closed_count": 6,
                "last_event_at": None,
                "last_reject_at": None,
                "last_fill_at": None,
            },
            "checks": [],
            "stale_pending_orders": [],
            "recent_rejections": [],
            "recent_fills": [],
            "recent_closed": [],
        }

        with (
            patch.object(trade_service.sdm, "read_open_trades", return_value=pd.DataFrame()),
            patch.object(trade_service.sdm, "read_pending_orders", return_value=pd.DataFrame()),
            patch.object(trade_service.sdm, "read_closed_trades", return_value=pd.DataFrame()),
            patch.object(trade_service.sdm, "monitor_open_trades", return_value=pd.DataFrame()),
            patch.object(trade_service.sdm, "trade_summary", return_value={"total_realized_pnl": 0}),
            patch.object(trade_service, "get_pending_orders_snapshot", return_value={"items": [], "count": 0, "order_events": {"items": [], "count": 0, "status_counts": {}}}),
            patch.object(trade_service, "get_order_events_snapshot", return_value={"items": [], "count": 0, "status_counts": {}}),
            patch.object(trade_service, "get_order_lifecycle_health_snapshot", return_value=lifecycle_health),
            patch.object(portfolio_service, "_build_validation_snapshot", return_value=validation_snapshot),
        ):
            payload = trade_service.get_trade_summary()

        validation_sample_check = next(
            check for check in payload["rollout_readiness"]["checks"] if check["key"] == "validation_sample"
        )
        self.assertEqual(payload["rollout_readiness"]["status"], "locked")
        self.assertFalse(payload["rollout_readiness"]["allows_live_rollout"])
        self.assertEqual(payload["rollout_readiness"]["metrics"]["current_route_orphan_order_event_count"], 2)
        self.assertEqual(payload["rollout_readiness"]["metrics"]["current_route_reconciliation_status"], "orphaned")
        self.assertEqual(validation_sample_check["tone"], "negative")
        self.assertIn("reconciliation", validation_sample_check["message"].lower())

    def test_ranked_entry_rollout_snapshot_blocks_when_validation_integrity_is_partial(self) -> None:
        from backend.services import portfolio_service
        from backend.services import strategy_validation_service

        summary_payload = {
            "generated_at": "2026-04-22T17:31:29+00:00",
            "starting_capital": 100000.0,
            "validation_integrity": {
                "metrics_source": "event_ledger",
                "mark_to_market_coverage_status": "partial_window",
                "ledger_snapshot_consistency": "unavailable",
                "current_route_sample_status": "insufficient",
                "basis": "Mark-to-market snapshot coverage is incomplete for the analyzed window, so ledger metrics remain authoritative.",
            },
            "current_route_validation_integrity": {
                "metrics_source": "event_ledger",
                "mark_to_market_coverage_status": "partial_window",
                "ledger_snapshot_consistency": "unavailable",
                "current_route_sample_status": "insufficient",
                "route_window_start": "2026-04-22T14:00:00+00:00",
                "route_window_end": "2026-04-22T16:00:00+00:00",
                "route_window_snapshot_count": 1,
                "basis": "Current-route snapshot coverage is still incomplete.",
            },
            "signal_execution_alignment": {
                "current_route_directional_fill_count": 6,
            },
            "current_route_execution_realism": {
                "closed_trade_count": 2,
                "stress_matrix": [
                    {"key": "A", "label": "Current settings", "metrics": {"ending_equity": 102000.0, "average_trade_profit": 120.0, "max_drawdown_pct": 10.0, "gross_exposure_peak": 90000.0}},
                    {"key": "M", "label": "Ranked-entry stack (1.5x gross cap)", "metrics": {"ending_equity": 103500.0, "average_trade_profit": 135.0, "max_drawdown_pct": 11.0, "gross_exposure_peak": 145000.0}},
                ],
            },
        }
        tracker_payload = {
            "checklist": [
                {
                    "key": "accounting",
                    "status": "partial",
                    "detail": "The ledger models fees explicitly, but mark-to-market coverage is still incomplete.",
                },
                {
                    "key": "execution_realism",
                    "status": "partial",
                    "detail": "The current post-fix route still lacks enough directional fill and close coverage to validate execution behavior.",
                },
                {
                    "key": "ranked_entry_rollout",
                    "status": "partial",
                    "detail": "Ranked-entry promotion remains blocked until accounting and execution realism both clear validation.",
                },
            ]
        }
        stress_matrix_payload = [{"key": "A", "label": "Audit matrix row", "metrics": {"ending_equity": 102000.0}}]

        with (
            patch.object(
                portfolio_service,
                "_read_validation_export_json",
                side_effect=[summary_payload, tracker_payload, stress_matrix_payload],
            ),
            patch.object(
                strategy_validation_service,
                "evaluate_ranked_entry_rollout_acceptance",
                return_value={
                    "accepted": True,
                    "status": "accepted",
                    "basis": "Candidate improves the baseline without breaching drawdown or gross-exposure limits.",
                    "baseline_key": "A",
                    "candidate_key": "M",
                    "baseline": {"ending_equity": 102000.0, "average_trade_profit": 120.0, "max_drawdown_pct": 10.0},
                    "candidate": {"ending_equity": 103500.0, "average_trade_profit": 135.0, "max_drawdown_pct": 11.0, "gross_exposure_peak": 145000.0},
                    "drawdown_limit_pct": 11.5,
                    "gross_cap_dollars": 150000.0,
                },
            ),
        ):
            snapshot = portfolio_service._build_ranked_entry_rollout_snapshot()

        self.assertFalse(snapshot["accepted"])
        self.assertEqual(snapshot["status"], "blocked")
        self.assertEqual(snapshot["metrics_source"], "event_ledger")
        self.assertEqual(snapshot["current_route_sample_status"], "insufficient")
        self.assertEqual(snapshot["route_window_start"], "2026-04-22T14:00:00+00:00")
        self.assertEqual(snapshot["route_window_snapshot_count"], 1)
        self.assertIn("blocked", snapshot["basis"].lower())

    def test_ranked_entry_rollout_snapshot_blocks_when_prediction_stack_validation_is_not_passed(self) -> None:
        from backend.services import portfolio_service
        from backend.services import strategy_validation_service

        summary_payload = {
            "generated_at": "2026-04-22T17:31:29+00:00",
            "starting_capital": 100000.0,
            "validation_integrity": {
                "metrics_source": "event_ledger",
                "mark_to_market_coverage_status": "complete",
                "ledger_snapshot_consistency": "consistent",
                "current_route_sample_status": "sufficient",
            },
            "current_route_validation_integrity": {
                "metrics_source": "event_ledger",
                "mark_to_market_coverage_status": "complete",
                "ledger_snapshot_consistency": "consistent",
                "current_route_sample_status": "sufficient",
                "route_window_start": "2026-04-22T14:00:00+00:00",
                "route_window_end": "2026-04-22T16:00:00+00:00",
                "route_window_snapshot_count": 6,
            },
            "signal_execution_alignment": {
                "current_route_directional_fill_count": 12,
            },
            "current_route_execution_realism": {
                "closed_trade_count": 6,
                "stress_matrix": [
                    {"key": "A", "label": "Current settings", "metrics": {"ending_equity": 102000.0, "average_trade_profit": 120.0, "max_drawdown_pct": 10.0, "gross_exposure_peak": 90000.0}},
                    {"key": "M", "label": "Ranked-entry stack (1.5x gross cap)", "metrics": {"ending_equity": 103500.0, "average_trade_profit": 135.0, "max_drawdown_pct": 11.0, "gross_exposure_peak": 145000.0}},
                ],
            },
            "broker_reconciliation": {
                "current_route_reconciliation_status": "reconciled",
                "current_route_orphan_order_event_count": 0,
                "legacy_orphan_order_event_count": 0,
            },
            "intraday_prediction_validation": {
                "status": "partial",
                "accepted": False,
                "basis": "full_hybrid unavailable; running no-pay tier. The active forecast-journal window does not yet have enough paired proxy-baseline and hybrid_stock_only rows to score the promotion tier.",
                "candidate_key": "hybrid_stock_only",
                "active_candidate_configuration": "hybrid_stock_only",
                "preferred_candidate_configuration": "hybrid_stock_only",
                "prediction_promotion_tier": "hybrid_stock_only",
            },
        }
        tracker_payload = {
            "checklist": [
                {"key": "accounting", "status": "pass", "detail": "pass"},
                {"key": "execution_realism", "status": "pass", "detail": "pass"},
                {"key": "ranked_entry_rollout", "status": "pass", "detail": "pass"},
            ]
        }
        stress_matrix_payload = [{"key": "A", "label": "Audit matrix row", "metrics": {"ending_equity": 102000.0}}]

        with (
            patch.object(
                portfolio_service,
                "_read_validation_export_json",
                side_effect=[summary_payload, tracker_payload, stress_matrix_payload],
            ),
            patch.object(
                strategy_validation_service,
                "evaluate_ranked_entry_rollout_acceptance",
                return_value={
                    "accepted": True,
                    "status": "accepted",
                    "basis": "Candidate improves the baseline without breaching drawdown or gross-exposure limits.",
                    "baseline_key": "A",
                    "candidate_key": "M",
                    "baseline": {"ending_equity": 102000.0, "average_trade_profit": 120.0, "max_drawdown_pct": 10.0},
                    "candidate": {"ending_equity": 103500.0, "average_trade_profit": 135.0, "max_drawdown_pct": 11.0, "gross_exposure_peak": 145000.0},
                    "drawdown_limit_pct": 11.5,
                    "gross_cap_dollars": 150000.0,
                },
            ),
        ):
            snapshot = portfolio_service._build_ranked_entry_rollout_snapshot()

        self.assertFalse(snapshot["accepted"])
        self.assertEqual(snapshot["status"], "blocked")
        self.assertIn("hybrid_stock_only", snapshot["basis"])
        self.assertEqual(snapshot["prediction_stack_validation"]["status"], "partial")
        self.assertEqual(snapshot["prediction_active_candidate_configuration"], "hybrid_stock_only")
        self.assertEqual(snapshot["prediction_promotion_tier"], "hybrid_stock_only")

    def test_ranked_entry_rollout_snapshot_accepts_hybrid_stock_only_prediction_tier(self) -> None:
        from backend.services import portfolio_service
        from backend.services import strategy_validation_service

        summary_payload = {
            "generated_at": "2026-04-22T17:31:29+00:00",
            "starting_capital": 100000.0,
            "validation_integrity": {
                "metrics_source": "event_ledger",
                "mark_to_market_coverage_status": "complete",
                "ledger_snapshot_consistency": "consistent",
                "current_route_sample_status": "sufficient",
            },
            "current_route_validation_integrity": {
                "metrics_source": "event_ledger",
                "mark_to_market_coverage_status": "complete",
                "ledger_snapshot_consistency": "consistent",
                "current_route_sample_status": "sufficient",
                "route_window_start": "2026-04-22T14:00:00+00:00",
                "route_window_end": "2026-04-22T16:00:00+00:00",
                "route_window_snapshot_count": 6,
            },
            "signal_execution_alignment": {
                "current_route_directional_fill_count": 12,
            },
            "current_route_execution_realism": {
                "closed_trade_count": 6,
                "stress_matrix": [
                    {"key": "A", "label": "Current settings", "metrics": {"ending_equity": 102000.0, "average_trade_profit": 120.0, "max_drawdown_pct": 10.0, "gross_exposure_peak": 90000.0}},
                    {"key": "M", "label": "Ranked-entry stack (1.5x gross cap)", "metrics": {"ending_equity": 103500.0, "average_trade_profit": 135.0, "max_drawdown_pct": 11.0, "gross_exposure_peak": 145000.0}},
                ],
            },
            "broker_reconciliation": {
                "current_route_reconciliation_status": "reconciled",
                "current_route_orphan_order_event_count": 0,
                "legacy_orphan_order_event_count": 0,
            },
            "intraday_prediction_validation": {
                "status": "pass",
                "accepted": True,
                "basis": "full_hybrid unavailable; running no-pay tier. hybrid_stock_only improves intraday edge without degrading calibration or drawdown beyond the allowed limit.",
                "candidate_key": "hybrid_stock_only",
                "active_candidate_configuration": "hybrid_stock_only",
                "preferred_candidate_configuration": "hybrid_stock_only",
                "prediction_promotion_tier": "hybrid_stock_only",
            },
        }
        tracker_payload = {
            "checklist": [
                {"key": "accounting", "status": "pass", "detail": "pass"},
                {"key": "execution_realism", "status": "pass", "detail": "pass"},
                {"key": "ranked_entry_rollout", "status": "pass", "detail": "pass"},
            ]
        }
        stress_matrix_payload = [{"key": "A", "label": "Audit matrix row", "metrics": {"ending_equity": 102000.0}}]

        with (
            patch.object(
                portfolio_service,
                "_read_validation_export_json",
                side_effect=[summary_payload, tracker_payload, stress_matrix_payload],
            ),
            patch.object(
                strategy_validation_service,
                "evaluate_ranked_entry_rollout_acceptance",
                return_value={
                    "accepted": True,
                    "status": "accepted",
                    "basis": "Candidate improves the baseline without breaching drawdown or gross-exposure limits.",
                    "baseline_key": "A",
                    "candidate_key": "M",
                    "baseline": {"ending_equity": 102000.0, "average_trade_profit": 120.0, "max_drawdown_pct": 10.0},
                    "candidate": {"ending_equity": 103500.0, "average_trade_profit": 135.0, "max_drawdown_pct": 11.0, "gross_exposure_peak": 145000.0},
                    "drawdown_limit_pct": 11.5,
                    "gross_cap_dollars": 150000.0,
                },
            ),
        ):
            snapshot = portfolio_service._build_ranked_entry_rollout_snapshot()

        self.assertTrue(snapshot["accepted"])
        self.assertEqual(snapshot["status"], "accepted")
        self.assertEqual(snapshot["prediction_active_candidate_configuration"], "hybrid_stock_only")
        self.assertEqual(snapshot["prediction_promotion_tier"], "hybrid_stock_only")
        self.assertIn("full_hybrid unavailable", snapshot["prediction_stack_validation"]["basis"])

    def test_trade_summary_reports_live_pilot_audit(self) -> None:
        from backend.services import portfolio_service, trade_service

        validation_snapshot = {
            "scorecards": [],
            "route_quality": {
                "clean_fill_count": 4,
                "slipped_fill_count": 0,
                "fragile_fill_count": 0,
                "rejected_route_count": 0,
                "partial_fill_count": 0,
                "average_abs_slippage_bps": 8.4,
                "latest_execution_review": None,
            },
            "board_snapshot_history": {"count": 0, "items": []},
            "replay_comparisons": {
                "board_outcomes": {
                    "count": 4,
                    "resolved_count": 4,
                    "open_count": 0,
                    "items": [
                        {"status": "resolved", "pnl_dollars": 120.0},
                        {"status": "resolved", "pnl_dollars": 45.0},
                        {"status": "resolved", "pnl_dollars": -15.0},
                        {"status": "resolved", "pnl_dollars": 30.0},
                    ],
                },
                "paper_live_slippage": {
                    "count": 4,
                    "average_signed_slippage_bps": 1.2,
                    "average_abs_slippage_bps": 8.4,
                    "worst_abs_slippage_bps": 18.0,
                    "items": [],
                },
            },
            "ranked_entry_rollout": {
                "available": True,
                "accepted": True,
                "status": "accepted",
                "basis": "Ranked-entry validation accepts profile M for live-gate review.",
                "baseline_key": "A",
                "candidate_key": "M",
                "baseline": {"ending_equity": 102000.0, "average_trade_profit": 120.0, "max_drawdown_pct": 10.0},
                "candidate": {"ending_equity": 103500.0, "average_trade_profit": 135.0, "max_drawdown_pct": 11.0, "gross_exposure_peak": 145000.0},
            },
        }
        lifecycle_health = {
            "summary": {
                "status": "healthy",
                "message": "Order lifecycle is healthy for the current pilot snapshot.",
                "pending_order_count": 0,
                "stale_pending_count": 0,
                "reject_count": 0,
                "fill_count": 0,
                "closed_count": 0,
                "last_event_at": None,
                "last_reject_at": None,
                "last_fill_at": None,
            },
            "checks": [],
            "stale_pending_orders": [],
            "recent_rejections": [],
            "recent_fills": [],
            "recent_closed": [],
        }
        order_events = {
            "items": [
                {
                    "id": "evt-live-1",
                    "trade_id": "trade-live-1",
                    "ticker": "SPY",
                    "event_key": "order.accepted",
                    "label": "Working",
                    "status": "working",
                    "detail": "Limit order accepted and is now working on the desk.",
                    "created_at": "2026-04-18T14:30:00Z",
                    "payload": {
                        "rollout_audit": {
                            "execution_intent": "broker_live",
                            "route_label": "Broker live",
                            "adapter": "alpaca_live",
                            "allows_live_rollout": True,
                            "gate_status": "ready",
                            "gate_tone": "positive",
                            "gate_label": "Pilot live ready",
                            "basis": "Resolved board leaders, execution replay, and benchmark context are all clearing inside policy 4 resolved | 55% win | <=10 avg bps | <=20 worst bps.",
                            "history_trend": "improving",
                            "history_label": "Improving",
                            "resolved_count": 4,
                            "open_count": 0,
                            "replay_win_rate": 0.75,
                            "slippage_sample_count": 4,
                            "average_abs_slippage_bps": 8.4,
                            "worst_abs_slippage_bps": 18.0,
                            "reject_count": 0,
                            "fragile_route_count": 0,
                        },
                    },
                },
            ],
            "count": 1,
            "status_counts": {"working": 1},
        }

        with (
            patch.object(trade_service.sdm, "read_open_trades", return_value=pd.DataFrame()),
            patch.object(trade_service.sdm, "read_pending_orders", return_value=pd.DataFrame()),
            patch.object(trade_service.sdm, "read_closed_trades", return_value=pd.DataFrame()),
            patch.object(trade_service.sdm, "monitor_open_trades", return_value=pd.DataFrame()),
            patch.object(trade_service, "get_pending_orders_snapshot", return_value={"items": [], "count": 0, "order_events": {"items": [], "count": 0, "status_counts": {}}}),
            patch.object(trade_service, "get_order_events_snapshot", return_value=order_events),
            patch.object(trade_service, "get_order_lifecycle_health_snapshot", return_value=lifecycle_health),
            patch.object(portfolio_service, "_build_validation_snapshot", return_value=validation_snapshot),
        ):
            payload = trade_service.get_trade_summary()

        self.assertEqual(payload["live_pilot_audit"]["count"], 1)
        self.assertEqual(payload["live_pilot_audit"]["allowed_count"], 1)
        self.assertEqual(payload["live_pilot_audit"]["label"], "Last live pilot working")
        self.assertEqual(payload["live_pilot_audit"]["latest"]["ticker"], "SPY")
        self.assertEqual(payload["live_pilot_audit"]["latest"]["gate_label"], "Pilot live ready")

    def test_sync_pending_orders_records_broker_fill_event(self) -> None:
        from backend.core.database import Base
        from backend.models.saas import OrderEventRecord
        from backend.services import tenant_service, trade_service
        from backend.services.execution.types import SyncOrderResult

        engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
        Session = sessionmaker(bind=engine, expire_on_commit=False, future=True)
        Base.metadata.create_all(bind=engine)

        fake_settings = SimpleNamespace(
            demo_tenant_slug="alpha-desk",
            demo_tenant_name="Alpha Desk",
            demo_tenant_plan="pro",
        )
        pending_order = {
            "trade_id": "trade-working-1",
            "order_id": "order-working-1",
            "ticker": "SPY",
            "order_type": "limit",
            "time_in_force": "day",
            "broker_name": "alpaca_paper",
            "broker_order_id": "broker-order-1",
            "broker_status": "new",
        }
        synced_open_record = {
            "trade_id": "trade-working-1",
            "order_id": "order-working-1",
            "ticker": "SPY",
            "order_type": "limit",
            "time_in_force": "day",
            "expected_fill_price": 500.5,
            "actual_fill_price": 501.25,
        }
        fake_adapter = MagicMock()
        fake_adapter.sync_order.return_value = SyncOrderResult(
            state="filled",
            opened_record=synced_open_record,
            broker_name="alpaca_paper",
            broker_order_id="broker-order-1",
            broker_status="filled",
            broker_response={"id": "broker-order-1", "status": "filled"},
            detail="Broker order filled and opened a live desk-tracked position.",
            slippage_dollars=0.75,
            slippage_bps=15.0,
        )

        with (
            Session() as db,
            patch.object(tenant_service, "settings", fake_settings),
            patch.object(trade_service.sdm, "read_pending_orders", return_value=pd.DataFrame([pending_order])),
            patch.object(trade_service, "get_execution_adapter_for", return_value=fake_adapter),
        ):
            identity = tenant_service.ensure_demo_tenant_for_user(
                db,
                auth_subject="demo-trader",
                email="demo@example.test",
                name="Demo Trader",
                provider="local-demo",
                requested_tenant_slug="alpha-desk",
            )
            current_user = SimpleNamespace(
                tenant_id=identity["active_tenant"].id,
                tenant_slug="alpha-desk",
                tenant_name="Alpha Desk",
                tenant_status="active",
                tenant_plan="pro",
                auth_subject="demo-trader",
                user_id="demo-trader",
            )

            payload = trade_service.sync_pending_orders_from_broker(
                db=db,
                current_user=current_user,
            )
            stored_events = db.execute(select(OrderEventRecord).order_by(OrderEventRecord.created_at.asc())).scalars().all()

        self.assertTrue(payload["synced"])
        self.assertEqual(payload["summary"]["filled"], 1)
        self.assertEqual(payload["items"][0]["state"], "filled")
        self.assertEqual(payload["items"][0]["slippage_bps"], 15.0)
        self.assertEqual(len(stored_events), 1)
        self.assertEqual(stored_events[0].event_key, "order.filled")
        self.assertEqual(payload["latest_order_event"]["event_key"], "order.filled")

    def test_replace_and_cancel_pending_order_record_lifecycle_events(self) -> None:
        from backend.core.database import Base
        from backend.models.saas import OrderEventRecord
        from backend.schemas import CancelOrderRequest, ReplaceOrderRequest
        from backend.services import tenant_service, trade_service

        engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
        Session = sessionmaker(bind=engine, expire_on_commit=False, future=True)
        Base.metadata.create_all(bind=engine)

        fake_settings = SimpleNamespace(
            demo_tenant_slug="alpha-desk",
            demo_tenant_name="Alpha Desk",
            demo_tenant_plan="pro",
        )
        pending_order = {
            "order_id": "order-working-1",
            "trade_id": "trade-working-1",
            "ticker": "SPY",
            "submitted_at": "2026-04-16T10:00:00+00:00",
            "updated_at": "2026-04-16T10:01:00+00:00",
            "order_type": "limit",
            "time_in_force": "day_ext",
            "status": "PENDING",
            "book_state": "pending",
        }
        replaced_order = {
            **pending_order,
            "order_type": "stop_limit",
            "limit_price": 500.5,
            "stop_price": 501.0,
            "updated_at": "2026-04-16T10:02:00+00:00",
        }

        with (
            Session() as db,
            patch.object(tenant_service, "settings", fake_settings),
            patch.object(trade_service.sdm, "read_pending_orders", return_value=pd.DataFrame([pending_order])),
            patch.object(trade_service.sdm, "replace_pending_order", return_value=replaced_order),
            patch.object(trade_service.sdm, "cancel_pending_order", return_value=replaced_order),
        ):
            identity = tenant_service.ensure_demo_tenant_for_user(
                db,
                auth_subject="demo-trader",
                email="demo@example.test",
                name="Demo Trader",
                provider="local-demo",
                requested_tenant_slug="alpha-desk",
            )
            current_user = SimpleNamespace(
                tenant_id=identity["active_tenant"].id,
                tenant_slug="alpha-desk",
                tenant_name="Alpha Desk",
                tenant_status="active",
                tenant_plan="pro",
                auth_subject="demo-trader",
                user_id="demo-trader",
            )

            replaced_payload = trade_service.replace_pending_order_from_request(
                "order-working-1",
                ReplaceOrderRequest(
                    order_type="stop_limit",
                    time_in_force="day_ext",
                    limit_price=500.5,
                    stop_price=501.0,
                    extended_hours=True,
                ),
                db=db,
                current_user=current_user,
            )
            canceled_payload = trade_service.cancel_pending_order_from_request(
                "order-working-1",
                CancelOrderRequest(reason="Operator canceled the staged order."),
                db=db,
                current_user=current_user,
            )
            stored_events = db.execute(select(OrderEventRecord).order_by(OrderEventRecord.created_at.asc())).scalars().all()

        self.assertTrue(replaced_payload["updated"])
        self.assertEqual(replaced_payload["latest_order_event"]["event_key"], "order.replaced")
        self.assertTrue(canceled_payload["canceled"])
        self.assertEqual(canceled_payload["latest_order_event"]["event_key"], "order.canceled")
        self.assertEqual([row.event_key for row in stored_events], ["order.replaced", "order.canceled"])

    def test_close_trade_records_closed_order_event(self) -> None:
        from backend.core.database import Base
        from backend.models.saas import OrderEventRecord
        from backend.schemas import CloseTradeRequest
        from backend.services import tenant_service, trade_service

        engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
        Session = sessionmaker(bind=engine, expire_on_commit=False, future=True)
        Base.metadata.create_all(bind=engine)

        fake_settings = SimpleNamespace(
            demo_tenant_slug="alpha-desk",
            demo_tenant_name="Alpha Desk",
            demo_tenant_plan="pro",
        )
        open_frame = pd.DataFrame(
            [
                {
                    "trade_id": "trade-close-1",
                    "ticker": "SPY",
                    "opened_at": "2026-04-16T10:00:00+00:00",
                    "order_type": "market",
                    "time_in_force": "day",
                }
            ]
        )

        with (
            Session() as db,
            patch.object(tenant_service, "settings", fake_settings),
            patch.object(trade_service.sdm, "read_open_trades", return_value=open_frame),
            patch.object(trade_service.sdm, "close_trade_by_index"),
        ):
            identity = tenant_service.ensure_demo_tenant_for_user(
                db,
                auth_subject="demo-trader",
                email="demo@example.test",
                name="Demo Trader",
                provider="local-demo",
                requested_tenant_slug="alpha-desk",
            )
            current_user = SimpleNamespace(
                tenant_id=identity["active_tenant"].id,
                tenant_slug="alpha-desk",
                tenant_name="Alpha Desk",
                tenant_status="active",
                tenant_plan="pro",
                auth_subject="demo-trader",
                user_id="demo-trader",
            )

            payload = trade_service.close_trade_from_request(
                CloseTradeRequest(
                    trade_index=0,
                    close_underlying_price=498.5,
                    close_contract_mid=3.1,
                ),
                db=db,
                current_user=current_user,
            )
            stored_events = db.execute(select(OrderEventRecord)).scalars().all()

        self.assertTrue(payload["closed"])
        self.assertEqual(payload["latest_order_event"]["status"], "closed")
        self.assertEqual(len(stored_events), 1)
        self.assertEqual(stored_events[0].event_key, "order.closed")

    def test_portfolio_snapshot_includes_latest_order_event_per_trade(self) -> None:
        from backend.core.database import Base
        from backend.models.saas import OrderEventRecord
        from backend.services import portfolio_service, tenant_service

        engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
        Session = sessionmaker(bind=engine, expire_on_commit=False, future=True)
        Base.metadata.create_all(bind=engine)

        fake_settings = SimpleNamespace(
            demo_tenant_slug="alpha-desk",
            demo_tenant_name="Alpha Desk",
            demo_tenant_plan="pro",
        )
        open_frame = pd.DataFrame(
            [
                {
                    "trade_id": "trade-live-1",
                    "ticker": "SPY",
                    "opened_at": "2026-04-16T10:00:00+00:00",
                    "live_price_at_open": 501.2,
                    "order_type": "limit",
                    "time_in_force": "day_ext",
                    "status": "OPEN",
                }
            ]
        )
        monitor_frame = pd.DataFrame(
            [
                {
                    "ticker": "SPY",
                    "opened_at": "2026-04-16T10:00:00+00:00",
                    "monitor_action": "HOLD",
                    "current_underlying": 502.1,
                }
            ]
        )

        with (
            Session() as db,
            patch.object(tenant_service, "settings", fake_settings),
            patch.object(portfolio_service.sdm, "read_open_trades", return_value=open_frame),
            patch.object(portfolio_service.sdm, "read_pending_orders", return_value=pd.DataFrame([
                {
                    "order_id": "order-working-1",
                    "trade_id": "trade-working-1",
                    "ticker": "QQQ",
                    "submitted_at": "2026-04-16T10:05:00+00:00",
                    "updated_at": "2026-04-16T10:06:00+00:00",
                    "order_type": "limit",
                    "time_in_force": "day_ext",
                    "status": "PENDING",
                }
            ])),
            patch.object(portfolio_service.sdm, "read_closed_trades", return_value=pd.DataFrame()),
            patch.object(portfolio_service.sdm, "monitor_open_trades", return_value=monitor_frame),
            patch.object(portfolio_service.sdm, "portfolio_summary", return_value={"open_trade_count": 1}),
            patch.object(portfolio_service.sdm, "trade_summary", return_value={"total_realized_pnl": 0}),
            patch.object(portfolio_service.sdm, "performance_analytics", return_value={}),
            patch.object(portfolio_service.sdm, "open_risk_dashboard", return_value={"status": "OK"}),
        ):
            identity = tenant_service.ensure_demo_tenant_for_user(
                db,
                auth_subject="demo-trader",
                email="demo@example.test",
                name="Demo Trader",
                provider="local-demo",
                requested_tenant_slug="alpha-desk",
            )
            tenant = identity["active_tenant"]
            db.add(
                OrderEventRecord(
                    tenant=tenant,
                    trade_id="trade-live-1",
                    ticker="SPY",
                    event_key="order.opened",
                    status="open",
                    order_type="limit",
                    time_in_force="day_ext",
                    route_state="accepted",
                    book_state="open",
                    detail="Limit order accepted and opened a desk-tracked position.",
                )
            )
            db.commit()
            current_user = SimpleNamespace(
                tenant_id=tenant.id,
                tenant_slug="alpha-desk",
                auth_subject="demo-trader",
                user_id="demo-trader",
            )

            payload = portfolio_service.get_portfolio(db=db, current_user=current_user)

        self.assertEqual(payload["order_events"]["count"], 1)
        self.assertEqual(payload["open_trades"][0]["trade_id"], "trade-live-1")
        self.assertEqual(payload["open_trades"][0]["latest_order_event"]["status"], "open")
        self.assertEqual(payload["monitored_open_trades"][0]["latest_order_event"]["trade_id"], "trade-live-1")
        self.assertEqual(payload["pending_orders"][0]["trade_id"], "trade-working-1")
        self.assertEqual(payload["capital_preservation"]["open_position_count"], 1)
        self.assertEqual(payload["capital_preservation"]["pending_order_count"], 1)
        self.assertIn("trade_summary", payload)

    def test_trade_journal_prefers_normalized_closed_trades(self) -> None:
        from backend.services import portfolio_service

        closed_trades = pd.DataFrame(
            [
                {
                    "trade_id": "trade-closed-1",
                    "ticker": "SPY",
                    "opened_at": "2026-04-15T14:30:00+00:00",
                    "closed_at": "2026-04-15T19:05:00+00:00",
                    "instrument_type": "listed_option",
                    "instrument_label": "Listed option",
                    "option_right": "call",
                    "verdict": "BULLISH",
                    "setup_grade": "A-",
                    "contract_symbol": "SPY260417C00560000",
                    "contract_mid_at_open": 2.5,
                    "contract_mid_at_close": 3.75,
                    "realized_pnl": 125.0,
                    "max_risk_dollars": 250.0,
                    "order_type": "limit",
                    "time_in_force": "day",
                    "expected_fill_price": 2.5,
                    "actual_fill_price": 2.52,
                    "fill_slippage_dollars": 0.02,
                    "fill_slippage_bps": 8.0,
                    "status": "CLOSED",
                }
            ]
        )

        legacy_journal = pd.DataFrame(
            [
                {
                    "timestamp": "2026-04-01T12:00:00+00:00",
                    "ticker": "QQQ",
                    "direction": "PUT",
                    "contract_symbol": "QQQ260401P00450000",
                    "pnl_dollars": -45.0,
                }
            ]
        )

        with (
            patch.object(portfolio_service.sdm, "read_closed_trades", return_value=closed_trades),
            patch.object(portfolio_service.sdm, "read_trade_journal", return_value=legacy_journal),
        ):
            payload = portfolio_service.get_trade_journal(limit=25, offset=0, search="", result_filter="all", direction_filter="all")

        self.assertEqual(payload["count"], 1)
        self.assertEqual(payload["journal"][0]["ticker"], "SPY")
        self.assertEqual(payload["journal"][0]["instrument_label"], "Listed option")
        self.assertEqual(payload["journal"][0]["direction"], "CALL")
        self.assertEqual(payload["journal"][0]["entry_contract_mid"], 2.5)
        self.assertEqual(payload["journal"][0]["close_contract_mid"], 3.75)
        self.assertEqual(payload["journal"][0]["pnl_dollars"], 125.0)
        self.assertEqual(payload["journal"][0]["result_label"], "Win")
        self.assertEqual(payload["journal"][0]["execution_review_label"], "Clean fill")
        self.assertEqual(payload["journal"][0]["attribution_label"], "Clean win")
        self.assertEqual(payload["journal"][0]["journal_source"], "closed_trade")

    def test_trade_journal_flags_execution_drift_and_sizing_reviews(self) -> None:
        from backend.services import portfolio_service

        closed_trades = pd.DataFrame(
            [
                {
                    "trade_id": "trade-closed-2",
                    "ticker": "AAPL",
                    "opened_at": "2026-04-16T14:30:00+00:00",
                    "closed_at": "2026-04-16T19:05:00+00:00",
                    "instrument_type": "equity",
                    "instrument_label": "Equity",
                    "verdict": "BULLISH",
                    "setup_grade": "A",
                    "contract_mid_at_open": 1.8,
                    "contract_mid_at_close": 2.15,
                    "realized_pnl": 35.0,
                    "max_risk_dollars": 60.0,
                    "order_type": "limit",
                    "expected_fill_price": 1.8,
                    "actual_fill_price": 1.81,
                    "fill_slippage_dollars": 0.01,
                    "fill_slippage_bps": 55.0,
                    "status": "CLOSED",
                },
                {
                    "trade_id": "trade-closed-3",
                    "ticker": "QQQ",
                    "opened_at": "2026-04-17T14:30:00+00:00",
                    "closed_at": "2026-04-17T19:05:00+00:00",
                    "instrument_type": "listed_option",
                    "instrument_label": "Listed option",
                    "option_right": "put",
                    "verdict": "BEARISH",
                    "setup_grade": "B",
                    "contract_mid_at_open": 2.2,
                    "contract_mid_at_close": 1.0,
                    "realized_pnl": -150.0,
                    "max_risk_dollars": 90.0,
                    "order_type": "limit",
                    "expected_fill_price": 2.2,
                    "actual_fill_price": 2.205,
                    "fill_slippage_dollars": 0.005,
                    "fill_slippage_bps": 4.0,
                    "status": "CLOSED",
                },
            ]
        )

        with patch.object(portfolio_service.sdm, "read_closed_trades", return_value=closed_trades):
            payload = portfolio_service.get_trade_journal(limit=25, offset=0, search="", result_filter="all", direction_filter="all")

        self.assertEqual(payload["count"], 2)
        by_trade_id = {row["trade_id"]: row for row in payload["journal"]}
        self.assertEqual(by_trade_id["trade-closed-2"]["execution_review_label"], "Fragile fill")
        self.assertEqual(by_trade_id["trade-closed-2"]["attribution_label"], "Thesis right / execution wrong")
        self.assertEqual(by_trade_id["trade-closed-3"]["execution_review_label"], "Clean fill")
        self.assertEqual(by_trade_id["trade-closed-3"]["attribution_label"], "Sizing wrong")

        with patch.object(portfolio_service.sdm, "read_closed_trades", return_value=closed_trades):
            execution_only = portfolio_service.get_trade_journal(
                limit=25,
                offset=0,
                search="",
                result_filter="all",
                direction_filter="all",
                attribution_filter="execution",
            )
            risk_only = portfolio_service.get_trade_journal(
                limit=25,
                offset=0,
                search="",
                result_filter="all",
                direction_filter="all",
                attribution_filter="risk",
            )

        self.assertEqual(execution_only["count"], 1)
        self.assertEqual(execution_only["journal"][0]["trade_id"], "trade-closed-2")
        self.assertEqual(execution_only["attribution_filter"], "execution")
        self.assertEqual(risk_only["count"], 1)
        self.assertEqual(risk_only["journal"][0]["trade_id"], "trade-closed-3")
        self.assertEqual(risk_only["attribution_filter"], "risk")

    def test_deployment_probe_routes_return_expected_status_codes(self) -> None:
        from backend.routers import system

        with (
            patch.object(
                system,
                "get_health",
                return_value={
                    "status": "ok",
                    "service": "Stock Options Signal Dashboard",
                    "version": "test",
                    "timestamp": "2026-04-17T12:00:00+00:00",
                },
            ),
            patch.object(
                system,
                "get_production_readiness_snapshot",
                return_value={
                    "summary": {
                        "status": "blocked",
                        "checked_at": "2026-04-17T12:01:00+00:00",
                        "blocked_checks": 2,
                        "warning_checks": 1,
                        "blockers": ["Database connectivity probe failed."],
                        "warnings": ["Billing needs operator review."],
                        "next_action": "Fix the database probe before accepting traffic.",
                    }
                },
            ),
        ):
            health_response = system.healthz()
            readiness_response = system.readyz()

        self.assertEqual(health_response.status_code, 200)
        self.assertEqual(readiness_response.status_code, 503)
        self.assertEqual(json.loads(health_response.body.decode("utf-8"))["probe"], "liveness")
        readiness_payload = json.loads(readiness_response.body.decode("utf-8"))
        self.assertEqual(readiness_payload["probe"], "readiness")
        self.assertFalse(readiness_payload["ready"])
        self.assertEqual(readiness_payload["blocked_checks"], 2)

    def test_support_diagnostics_export_returns_attachment_bundle(self) -> None:
        from backend.routers import system

        current_user = SimpleNamespace(user_id="demo-trader", tenant_slug="alpha-desk")
        with patch.object(
            system,
            "get_support_diagnostics_export",
            return_value={
                "generated_at": "2026-04-17T12:00:00+00:00",
                "capture": {"format": "personal-desk-support-v1"},
                "release": {"version": "test"},
                "release_notes": {"milestones": []},
                "ops": {"health": {"status": "ok"}},
            },
        ):
            response = system.operations_diagnostics(current_user=current_user)

        self.assertEqual(response.status_code, 200)
        self.assertIn("attachment; filename=", response.headers["content-disposition"])
        payload = json.loads(response.body.decode("utf-8"))
        self.assertEqual(payload["capture"]["format"], "personal-desk-support-v1")
        self.assertEqual(payload["ops"]["health"]["status"], "ok")

    def test_phase_a_exit_snapshot_rolls_up_tracker_docs_and_live_checks(self) -> None:
        from backend.services import frontend_service

        with _workspace_tempdir() as temp_dir:
            root = Path(temp_dir)
            tracker_path = root / "PERSONAL_USE.md"
            tracker_path.write_text(
                "\n".join(
                    [
                        "# Personal Readiness Checklist",
                        "",
                        "1. `[x]` Complete probe endpoints.",
                        "2. `[x]` Complete diagnostics export.",
                        "3. `[x]` Complete reliability tests.",
                        "4. `[x]` Publish real-money execution roadmap.",
                    ]
                ),
                encoding="utf-8",
            )
            docs_dir = root / "docs"
            runbooks_dir = docs_dir / "runbooks"
            reports_dir = docs_dir / "reports"
            runbooks_dir.mkdir(parents=True, exist_ok=True)
            reports_dir.mkdir(parents=True, exist_ok=True)
            (root / "REAL_MONEY_EXECUTION_ROADMAP.md").write_text("# roadmap\n", encoding="utf-8")

            with (
                patch.object(frontend_service, "PROJECT_ROOT", root),
                patch.object(frontend_service, "PHASE_A_TRACKER_PATH", tracker_path),
                patch.object(frontend_service, "PHASE_A_GO_LIVE_PATH", root / "PERSONAL_USE.md"),
                patch.object(frontend_service, "PHASE_A_EXIT_REPORT_PATH", root / "REAL_MONEY_EXECUTION_ROADMAP.md"),
            ):
                snapshot = frontend_service.get_phase_a_exit_snapshot(
                    tenant_slug="alpha-desk",
                    readiness_snapshot={"summary": {"status": "ready", "next_action": "Pilot readiness is clear."}},
                    release_gates={"summary": {"status": "ready", "next_action": "Release gates are clear."}},
                    deployment_snapshot={"summary": {"status": "warning", "next_action": "Rerun the restore drill before launch."}},
                    service_smoke={"summary": {"status": "ready", "next_action": "Core service smoke is healthy."}},
                )

        self.assertEqual(snapshot["summary"]["status"], "warning")
        self.assertEqual(snapshot["summary"]["tracker_completed"], 4)
        self.assertEqual(snapshot["summary"]["tracker_total"], 4)
        self.assertEqual(snapshot["tracker"]["queued_count"], 0)
        self.assertEqual(snapshot["docs"][0]["status"], "ready")
        self.assertTrue(any(item["key"] == "deployment_readiness" and item["status"] == "warning" for item in snapshot["checklist"]))
        self.assertEqual(snapshot["probe_endpoints"]["readiness"], "/api/readyz")

    def test_core_pilot_probe_and_diagnostics_routes_handle_repeated_calls(self) -> None:
        from backend.routers import system

        current_user = SimpleNamespace(user_id="demo-trader", tenant_slug="alpha-desk")
        readiness_snapshot = {
            "summary": {
                "status": "warning",
                "checked_at": "2026-04-17T12:01:00+00:00",
                "blocked_checks": 0,
                "warning_checks": 1,
                "blockers": [],
                "warnings": ["Billing needs operator review."],
                "next_action": "Billing needs operator review.",
            }
        }
        diagnostics_payload = {
            "generated_at": "2026-04-17T12:00:00+00:00",
            "capture": {"format": "personal-desk-support-v1"},
            "release": {"version": "test"},
            "release_notes": {"milestones": []},
            "ops": {"health": {"status": "ok"}, "phase_a": {"summary": {"status": "warning"}}},
        }

        with (
            patch.object(
                system,
                "get_health",
                return_value={
                    "status": "ok",
                    "service": "Stock Options Signal Dashboard",
                    "version": "test",
                    "timestamp": "2026-04-17T12:00:00+00:00",
                },
            ),
            patch.object(system, "get_production_readiness_snapshot", return_value=readiness_snapshot),
            patch.object(system, "get_support_diagnostics_export", return_value=diagnostics_payload),
        ):
            for _ in range(12):
                self.assertEqual(system.healthz().status_code, 200)
                self.assertEqual(system.readyz().status_code, 200)
                diagnostics_response = system.operations_diagnostics(current_user=current_user)
                self.assertEqual(diagnostics_response.status_code, 200)
                payload = json.loads(diagnostics_response.body.decode("utf-8"))
                self.assertEqual(payload["capture"]["format"], "personal-desk-support-v1")

    def test_json_writes_are_readable_after_atomic_replace(self) -> None:
        with _workspace_tempdir() as temp_dir:
            file_path = Path(temp_dir) / "store.json"
            write_json_file(file_path, {"items": [1, 2, 3]})
            self.assertEqual(read_json_file(file_path, {}), {"items": [1, 2, 3]})

    def test_close_trade_by_index_supports_partial_closes(self) -> None:
        from backend import stock_direction_model as sdm

        with _workspace_tempdir() as temp_dir:
            open_path = Path(temp_dir) / "open.csv"
            closed_path = Path(temp_dir) / "closed.csv"
            pd.DataFrame(
                [
                    {
                        "trade_id": "trade-partial-1",
                        "ticker": "SPY",
                        "instrument_type": "equity",
                        "contract_mid_at_open": 1.0,
                        "suggested_contracts": 4.0,
                        "position_cost": 400.0,
                        "max_risk_dollars": 120.0,
                        "status": "OPEN",
                    }
                ]
            ).to_csv(open_path, index=False)

            sdm.close_trade_by_index(
                trade_index=0,
                close_underlying_price=110.0,
                close_contract_mid=1.1,
                close_fraction=0.5,
                file_path_open=open_path,
                file_path_closed=closed_path,
            )

            remaining = pd.read_csv(open_path)
            closed = pd.read_csv(closed_path)

        self.assertEqual(len(remaining), 1)
        self.assertAlmostEqual(float(remaining.loc[0, "suggested_contracts"]), 2.0)
        self.assertAlmostEqual(float(remaining.loc[0, "position_cost"]), 200.0)
        self.assertAlmostEqual(float(remaining.loc[0, "max_risk_dollars"]), 60.0)
        self.assertEqual(len(closed), 1)
        self.assertEqual(str(closed.loc[0, "status"]), "PARTIAL")
        self.assertAlmostEqual(float(closed.loc[0, "closed_contracts"]), 2.0)
        self.assertAlmostEqual(float(closed.loc[0, "remaining_contracts_after_close"]), 2.0)
        self.assertAlmostEqual(float(closed.loc[0, "close_fraction"]), 0.5)
        self.assertAlmostEqual(float(closed.loc[0, "realized_pnl"]), 20.0)

    def test_evaluate_trade_alerts_tolerates_missing_option_plan_levels(self) -> None:
        from backend import stock_direction_model as sdm

        report = {
            "option_plan": {
                "entry_low_price": None,
                "entry_high_price": None,
                "expected_underlying_target": None,
                "invalidation_price": None,
            },
            "verdict": "BULLISH",
            "event_risk": False,
            "setup_score": 72,
            "setup_grade": "A setup",
            "conviction_label": "HIGH CONVICTION",
        }

        alerts = sdm.evaluate_trade_alerts(report, live_price=709.49)

        self.assertIn("HIGH SCORE SETUP", alerts)
        self.assertIn("A-GRADE SETUP", alerts)
        self.assertIn("HIGH CONVICTION", alerts)

    def test_position_management_plan_respects_milestones(self) -> None:
        from backend.services.trade_automation_service import _build_position_management_plan

        base_trade = {
            "ticker": "SPY",
            "broker_name": "desk",
            "suggested_contracts": 4.0,
        }

        tp1_plan = _build_position_management_plan("SELL 50% NOW", base_trade)
        self.assertIsNotNone(tp1_plan)
        self.assertAlmostEqual(float(tp1_plan["close_fraction"]), 0.5)
        self.assertTrue(tp1_plan["mark_tp1"])
        self.assertFalse(tp1_plan["mark_tp2"])

        duplicate_tp1 = _build_position_management_plan(
            "SELL 50% NOW",
            {**base_trade, "automation_tp1_taken_at": "2026-04-19T13:00:00+00:00"},
        )
        self.assertIsNone(duplicate_tp1)

        catch_up_tp2 = _build_position_management_plan("SELL MORE NOW", base_trade)
        self.assertIsNotNone(catch_up_tp2)
        self.assertAlmostEqual(float(catch_up_tp2["close_fraction"]), 0.75)
        self.assertTrue(catch_up_tp2["mark_tp1"])
        self.assertTrue(catch_up_tp2["mark_tp2"])

        runner_tp2 = _build_position_management_plan(
            "SELL MORE NOW",
            {**base_trade, "automation_tp1_taken_at": "2026-04-19T13:00:00+00:00"},
        )
        self.assertIsNotNone(runner_tp2)
        self.assertAlmostEqual(float(runner_tp2["close_fraction"]), 0.5)
        self.assertFalse(runner_tp2["mark_tp1"])
        self.assertTrue(runner_tp2["mark_tp2"])

        time_stop_plan = _build_position_management_plan("TIME STOP", base_trade)
        self.assertIsNotNone(time_stop_plan)
        self.assertAlmostEqual(float(time_stop_plan["close_fraction"]), 1.0)
        self.assertEqual(time_stop_plan["status"], "closed")
        self.assertEqual(time_stop_plan["reason"], "time_stop")

    def test_trade_automation_performance_snapshot_tracks_outcomes_and_drift(self) -> None:
        from backend.core.database import Base
        from backend.models.saas import OrderEventRecord
        from backend.services import tenant_service, trade_automation_service

        engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
        Session = sessionmaker(bind=engine, expire_on_commit=False, future=True)
        Base.metadata.create_all(bind=engine)

        fake_settings = SimpleNamespace(
            demo_tenant_slug="alpha-desk",
            demo_tenant_name="Alpha Desk",
            demo_tenant_plan="pro",
        )
        with Session() as db, patch.object(tenant_service, "settings", fake_settings):
            identity = tenant_service.ensure_demo_tenant_for_user(
                db,
                auth_subject="demo-trader",
                email="demo@example.test",
                name="Demo Trader",
                provider="local-demo",
                requested_tenant_slug="alpha-desk",
            )
            tenant = identity["active_tenant"]
            automation_closed = pd.DataFrame(
                [
                    {
                        "trade_id": "auto-trade-1",
                        "ticker": "SPY",
                        "automation_origin": "trade_automation",
                        "automation_tenant_id": tenant.id,
                        "realized_pnl": 120.0,
                        "closed_at": "2026-04-19T15:45:00+00:00",
                        "status": "CLOSED",
                        "close_fraction": 1.0,
                        "automation_execution_intent": "broker_paper",
                    },
                    {
                        "trade_id": "auto-trade-2",
                        "ticker": "QQQ",
                        "automation_origin": "trade_automation",
                        "automation_tenant_id": tenant.id,
                        "realized_pnl": -40.0,
                        "closed_at": "2026-04-19T15:15:00+00:00",
                        "status": "PARTIAL",
                        "close_fraction": 0.5,
                        "automation_execution_intent": "broker_paper",
                    },
                ]
            )

            db.add(
                OrderEventRecord(
                    tenant_id=tenant.id,
                    trade_id="auto-trade-1",
                    ticker="SPY",
                    event_key="order.filled",
                    status="filled",
                    detail="Automation filled the order.",
                    payload_json={"slippage_bps": 8.5},
                )
            )
            db.add(
                OrderEventRecord(
                    tenant_id=tenant.id,
                    trade_id="auto-trade-2",
                    ticker="QQQ",
                    event_key="order.partially_closed",
                    status="partially_closed",
                    detail="Automation trimmed the position.",
                    payload_json={"automation_cycle_id": "cycle-1"},
                )
            )
            db.commit()

            with patch.object(trade_automation_service.sdm, "read_closed_trades", return_value=automation_closed):
                snapshot = trade_automation_service._build_trade_automation_performance_snapshot(
                    db,
                    tenant=tenant,
                    state={
                        "runtime": {
                            "cycle_count": 6,
                            "success_count": 4,
                            "error_count": 0,
                            "rejection_count": 2,
                        }
                    },
                    owned_open=pd.DataFrame(),
                    owned_pending=pd.DataFrame(),
                )

        self.assertEqual(snapshot["metrics"]["closed_trade_count"], 2)
        self.assertAlmostEqual(snapshot["metrics"]["total_pnl"], 80.0)
        self.assertAlmostEqual(snapshot["metrics"]["slippage_sample_count"], 1)
        self.assertAlmostEqual(snapshot["metrics"]["average_abs_slippage_bps"], 8.5)
        self.assertEqual(snapshot["metrics"]["partial_exit_count"], 1)
        self.assertEqual(len(snapshot["recent_closed"]), 2)
        self.assertEqual(len(snapshot["recent_events"]), 2)
        self.assertEqual(snapshot["status"]["tone"], "warning")

    def test_trade_automation_guardrail_snapshot_tracks_daily_loss_and_caps(self) -> None:
        from backend.core.database import Base
        from backend.services import tenant_service, trade_automation_service

        engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
        Session = sessionmaker(bind=engine, expire_on_commit=False, future=True)
        Base.metadata.create_all(bind=engine)

        fake_settings = SimpleNamespace(
            demo_tenant_slug="alpha-desk",
            demo_tenant_name="Alpha Desk",
            demo_tenant_plan="pro",
        )
        with Session() as db, patch.object(tenant_service, "settings", fake_settings):
            identity = tenant_service.ensure_demo_tenant_for_user(
                db,
                auth_subject="demo-trader",
                email="demo@example.test",
                name="Demo Trader",
                provider="local-demo",
                requested_tenant_slug="alpha-desk",
            )
            tenant = identity["active_tenant"]
            state = trade_automation_service._read_trade_automation_state(tenant)
            state["settings"].update(
                {
                    "account_size": 10000.0,
                    "risk_percent": 0.25,
                    "max_daily_loss_r": 1.0,
                    "max_consecutive_losses": 3,
                    "max_daily_entries": 3,
                    "max_daily_entries_per_symbol": 1,
                    "max_total_open_notional": 5000.0,
                    "max_error_streak": 3,
                }
            )
            state["runtime"]["error_streak"] = 1
            state["history"] = [
                {"type": "open_trade", "ticker": "SPY", "at": "2026-04-20T14:00:00+00:00"},
                {"type": "open_trade", "ticker": "SPY", "at": "2026-04-20T13:45:00+00:00"},
            ]

            owned_open = pd.DataFrame([{"ticker": "SPY", "total_position_cost": 3000.0}])
            owned_pending = pd.DataFrame([{"ticker": "QQQ", "total_position_cost": 2500.0}])
            owned_closed = pd.DataFrame(
                [
                    {
                        "trade_id": "auto-loss-1",
                        "ticker": "SPY",
                        "automation_origin": "trade_automation",
                        "automation_tenant_id": tenant.id,
                        "automation_profile_key": "personal_paper",
                        "realized_pnl": -40.0,
                        "closed_at": "2026-04-20T15:00:00+00:00",
                    },
                    {
                        "trade_id": "auto-loss-2",
                        "ticker": "QQQ",
                        "automation_origin": "trade_automation",
                        "automation_tenant_id": tenant.id,
                        "automation_profile_key": "personal_paper",
                        "realized_pnl": -15.0,
                        "closed_at": "2026-04-17T15:00:00+00:00",
                    },
                ]
            )

            with (
                patch.object(trade_automation_service, "_utc_now", return_value=datetime(2026, 4, 20, 16, 0, tzinfo=timezone.utc)),
                patch.object(trade_automation_service.sdm, "read_closed_trades", return_value=owned_closed),
            ):
                snapshot = trade_automation_service._build_trade_automation_guardrail_snapshot(
                    tenant=tenant,
                    state=state,
                    profile_key="personal_paper",
                    owned_open=owned_open,
                    owned_pending=owned_pending,
                )

        self.assertTrue(snapshot["status"]["locked"])
        self.assertEqual(snapshot["status"]["reason"], "daily_loss_lock")
        self.assertAlmostEqual(snapshot["metrics"]["today_realized_pnl"], -40.0)
        self.assertAlmostEqual(snapshot["metrics"]["max_daily_loss_dollars"], 25.0)
        self.assertEqual(snapshot["metrics"]["entries_today"], 2)
        self.assertEqual(snapshot["entries_by_ticker"]["SPY"], 2)
        self.assertAlmostEqual(snapshot["metrics"]["open_notional"], 5500.0)

    def test_trade_automation_guardrail_snapshot_resets_loss_streak_for_new_session_day(self) -> None:
        from backend.core.database import Base
        from backend.services import tenant_service, trade_automation_service

        engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
        Session = sessionmaker(bind=engine, expire_on_commit=False, future=True)
        Base.metadata.create_all(bind=engine)

        fake_settings = SimpleNamespace(
            demo_tenant_slug="alpha-desk",
            demo_tenant_name="Alpha Desk",
            demo_tenant_plan="pro",
        )
        with Session() as db, patch.object(tenant_service, "settings", fake_settings):
            identity = tenant_service.ensure_demo_tenant_for_user(
                db,
                auth_subject="demo-trader",
                email="demo@example.test",
                name="Demo Trader",
                provider="local-demo",
                requested_tenant_slug="alpha-desk",
            )
            tenant = identity["active_tenant"]
            state = trade_automation_service._read_trade_automation_state(tenant)
            state["settings"].update(
                {
                    "account_size": 10000.0,
                    "risk_percent": 0.25,
                    "max_daily_loss_r": 2.0,
                    "max_consecutive_losses": 3,
                    "max_daily_entries": 3,
                    "max_daily_entries_per_symbol": 1,
                    "max_total_open_notional": 5000.0,
                    "max_error_streak": 3,
                }
            )

            owned_closed = pd.DataFrame(
                [
                    {
                        "trade_id": "prior-loss-1",
                        "ticker": "SPY",
                        "automation_origin": "trade_automation",
                        "automation_tenant_id": tenant.id,
                        "automation_profile_key": "personal_paper",
                        "realized_pnl": -20.0,
                        "closed_at": "2026-04-22T19:55:00+00:00",
                    },
                    {
                        "trade_id": "prior-loss-2",
                        "ticker": "QQQ",
                        "automation_origin": "trade_automation",
                        "automation_tenant_id": tenant.id,
                        "automation_profile_key": "personal_paper",
                        "realized_pnl": -15.0,
                        "closed_at": "2026-04-22T19:40:00+00:00",
                    },
                    {
                        "trade_id": "prior-loss-3",
                        "ticker": "AAPL",
                        "automation_origin": "trade_automation",
                        "automation_tenant_id": tenant.id,
                        "automation_profile_key": "personal_paper",
                        "realized_pnl": -10.0,
                        "closed_at": "2026-04-22T19:25:00+00:00",
                    },
                ]
            )

            with (
                patch.object(trade_automation_service, "_utc_now", return_value=datetime(2026, 4, 23, 13, 45, tzinfo=timezone.utc)),
                patch.object(trade_automation_service.sdm, "read_closed_trades", return_value=owned_closed),
            ):
                snapshot = trade_automation_service._build_trade_automation_guardrail_snapshot(
                    tenant=tenant,
                    state=state,
                    profile_key="personal_paper",
                    owned_open=pd.DataFrame(),
                    owned_pending=pd.DataFrame(),
                )

        self.assertFalse(snapshot["status"]["locked"])
        self.assertIsNone(snapshot["status"]["reason"])
        self.assertEqual(snapshot["metrics"]["consecutive_losses"], 0)
        self.assertEqual(snapshot["metrics"]["today_closed_count"], 0)

    def test_trade_automation_effective_funds_multiplier_uses_buying_power_cap(self) -> None:
        from backend.services import trade_automation_service

        resolved = trade_automation_service._resolve_account_effective_funds(
            {
                "equity": 100000.0,
                "cash": 100000.0,
                "portfolio_value": 100000.0,
                "buying_power": 401540.64,
            },
            multiplier=1.5,
        )

        self.assertAlmostEqual(resolved["actual_funds"], 100000.0)
        self.assertEqual(resolved["actual_funds_source"], "equity")
        self.assertAlmostEqual(resolved["effective_funds"], 150000.0)
        self.assertEqual(resolved["funds_source"], "equity")
        self.assertEqual(resolved["effective_funds_multiplier"], 1.5)
        self.assertIsNone(resolved["effective_funds_cap_source"])

    def test_trade_automation_state_refreshes_stale_tenant_metadata_before_worker_reads(self) -> None:
        from backend.core.database import Base
        from backend.models.saas import Tenant
        from backend.services import tenant_service, trade_automation_service

        engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
        Session = sessionmaker(bind=engine, expire_on_commit=False, future=True)
        Base.metadata.create_all(bind=engine)

        fake_settings = SimpleNamespace(
            demo_tenant_slug="alpha-desk",
            demo_tenant_name="Alpha Desk",
            demo_tenant_plan="pro",
        )

        with (
            Session() as session_a,
            Session() as session_b,
            patch.object(tenant_service, "settings", fake_settings),
        ):
            identity = tenant_service.ensure_demo_tenant_for_user(
                session_a,
                auth_subject="demo-trader",
                email="demo@example.test",
                name="Demo Trader",
                provider="local-demo",
                requested_tenant_slug="alpha-desk",
            )
            tenant_a = identity["active_tenant"]
            state_a = trade_automation_service._read_trade_automation_state(tenant_a)
            self.assertEqual(state_a["settings"]["tickers"], ["SPY", "QQQ", "AAPL", "MSFT"])

            tenant_b = session_b.execute(select(Tenant).where(Tenant.id == tenant_a.id)).scalar_one()
            state_b = trade_automation_service._read_trade_automation_state(tenant_b)
            state_b["settings"].update(
                {
                    "tickers": ["SPY", "QQQ", "IWM", "DIA", "XLK", "XLF"],
                    "max_open_positions": 3,
                    "max_daily_entries": 6,
                    "cooldown_minutes": 15,
                    "cycle_entry_rank_limit": 3,
                    "effective_funds_multiplier": 1.5,
                }
            )
            trade_automation_service._write_trade_automation_state(
                tenant_b,
                state_b,
                profile_key="personal_paper",
            )
            session_b.commit()

            refreshed_state = trade_automation_service._read_trade_automation_state(tenant_a)

        self.assertEqual(refreshed_state["settings"]["tickers"], ["SPY", "QQQ", "IWM", "DIA", "XLK", "XLF"])
        self.assertEqual(refreshed_state["settings"]["max_open_positions"], 3)
        self.assertEqual(refreshed_state["settings"]["max_daily_entries"], 6)
        self.assertEqual(refreshed_state["settings"]["cooldown_minutes"], 15)
        self.assertEqual(refreshed_state["settings"]["cycle_entry_rank_limit"], 3)
        self.assertEqual(refreshed_state["settings"]["effective_funds_multiplier"], 1.5)

    def test_tenant_metadata_slice_write_preserves_newer_trade_automation_profiles(self) -> None:
        from backend.core.database import Base
        from backend.models.saas import Tenant
        from backend.services import tenant_service, trade_automation_service

        engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
        Session = sessionmaker(bind=engine, expire_on_commit=False, future=True)
        Base.metadata.create_all(bind=engine)

        fake_settings = SimpleNamespace(
            demo_tenant_slug="alpha-desk",
            demo_tenant_name="Alpha Desk",
            demo_tenant_plan="pro",
        )

        with (
            Session() as session_a,
            Session() as session_b,
            patch.object(tenant_service, "settings", fake_settings),
        ):
            identity = tenant_service.ensure_demo_tenant_for_user(
                session_a,
                auth_subject="demo-trader",
                email="demo@example.test",
                name="Demo Trader",
                provider="local-demo",
                requested_tenant_slug="alpha-desk",
            )
            tenant_a = identity["active_tenant"]
            _ = trade_automation_service._read_trade_automation_state(tenant_a)

            tenant_b = session_b.execute(select(Tenant).where(Tenant.id == tenant_a.id)).scalar_one()
            state_b = trade_automation_service._read_trade_automation_state(tenant_b)
            state_b["settings"].update(
                {
                    "tickers": ["SPY", "QQQ", "IWM", "DIA", "XLK", "XLF"],
                    "max_open_positions": 3,
                    "max_daily_entries": 6,
                    "cooldown_minutes": 15,
                    "cycle_entry_rank_limit": 3,
                    "effective_funds_multiplier": 1.5,
                }
            )
            trade_automation_service._write_trade_automation_state(
                tenant_b,
                state_b,
                profile_key="personal_paper",
            )
            session_b.commit()

            usage = tenant_service._read_api_usage_state(tenant_a)
            usage["counters"]["total_requests"] = 7
            tenant_service._write_api_usage_state(tenant_a, usage)
            session_a.commit()

            reloaded_tenant = session_b.execute(select(Tenant).where(Tenant.id == tenant_a.id)).scalar_one()
            refreshed_state = trade_automation_service._read_trade_automation_state(reloaded_tenant)
            refreshed_usage = tenant_service._read_api_usage_state(reloaded_tenant)

        self.assertEqual(refreshed_state["settings"]["tickers"], ["SPY", "QQQ", "IWM", "DIA", "XLK", "XLF"])
        self.assertEqual(refreshed_state["settings"]["max_open_positions"], 3)
        self.assertEqual(refreshed_state["settings"]["max_daily_entries"], 6)
        self.assertEqual(refreshed_state["settings"]["cooldown_minutes"], 15)
        self.assertEqual(refreshed_state["settings"]["cycle_entry_rank_limit"], 3)
        self.assertEqual(refreshed_state["settings"]["effective_funds_multiplier"], 1.5)
        self.assertEqual(refreshed_usage["counters"]["total_requests"], 7)

    def test_trade_automation_cycle_keeps_drawdown_on_actual_equity_when_multiplier_is_higher(self) -> None:
        from backend.core.database import Base
        from backend.services import tenant_service, trade_automation_service

        engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
        Session = sessionmaker(bind=engine, expire_on_commit=False, future=True)
        Base.metadata.create_all(bind=engine)

        fake_settings = SimpleNamespace(
            demo_tenant_slug="alpha-desk",
            demo_tenant_name="Alpha Desk",
            demo_tenant_plan="pro",
        )
        fixed_now = datetime(2026, 4, 23, 15, 0, tzinfo=timezone.utc)
        with (
            Session() as db,
            patch.object(tenant_service, "settings", fake_settings),
        ):
            identity = tenant_service.ensure_demo_tenant_for_user(
                db,
                auth_subject="demo-trader",
                email="demo@example.test",
                name="Demo Trader",
                provider="local-demo",
                requested_tenant_slug="alpha-desk",
            )
            tenant = identity["active_tenant"]
            state = trade_automation_service._read_trade_automation_state(tenant)
            state["settings"].update(
                {
                    "enabled": True,
                    "armed": True,
                    "kill_switch": False,
                    "tickers": ["SPY"],
                    "execution_intent": "broker_paper",
                    "effective_funds_multiplier": 1.5,
                }
            )

            with (
                patch.object(trade_automation_service, "_utc_now", return_value=fixed_now),
                patch.object(trade_automation_service, "get_trade_summary", return_value={"rollout_readiness": {}}),
                patch.object(trade_automation_service, "sync_pending_orders_from_broker", return_value=None),
                patch.object(trade_automation_service, "_manage_automation_positions", return_value={"acted_count": 0, "failed_count": 0, "items": [], "failed_items": []}),
                patch.object(trade_automation_service, "_persist_watchlist_validation_snapshot", return_value=None),
                patch.object(trade_automation_service, "build_watchlist", return_value={"rows": [], "path_evaluations": []}),
                patch.object(trade_automation_service.sdm, "read_open_trades", return_value=pd.DataFrame()),
                patch.object(trade_automation_service.sdm, "read_pending_orders", return_value=pd.DataFrame()),
                patch.object(trade_automation_service.sdm, "read_closed_trades", return_value=pd.DataFrame()),
                patch.object(trade_automation_service.sdm, "monitor_open_trades", return_value=pd.DataFrame()),
                patch.object(trade_automation_service, "record_trade_automation_equity_snapshot", return_value={"cycle_id": "cycle-1", "cycle_at": fixed_now.isoformat()}),
                patch.object(
                    trade_automation_service,
                    "_build_live_current_route_collection_metrics",
                    return_value={
                        "current_route_fill_count": 0,
                        "current_route_closed_trade_count": 0,
                        "current_route_mismatched_count": 0,
                        "current_route_sample_status": "insufficient",
                        "mark_to_market_coverage_status": "missing",
                        "ledger_snapshot_consistency": "unavailable",
                        "metrics_source": "event_ledger",
                        "route_window_start": None,
                        "route_window_end": None,
                        "route_window_snapshot_count": 0,
                        "current_route_latest_event_at": None,
                        "current_route_validation_integrity": {},
                    },
                ),
                patch.object(
                    trade_automation_service,
                    "_resolve_trade_automation_profile_account_context",
                    return_value={
                        "profile_key": "personal_paper",
                        "scope": "personal_paper",
                        "linked_account": None,
                        "account_summary": {
                            "equity": 100000.0,
                            "cash": 100000.0,
                            "portfolio_value": 100000.0,
                            "buying_power": 400000.0,
                        },
                        "actual_funds": 100000.0,
                        "actual_funds_source": "equity",
                        "effective_funds": 150000.0,
                        "funds_source": "equity",
                        "effective_funds_multiplier": 1.5,
                        "effective_funds_cap_source": None,
                        "effective_funds_detail": "Deployable sizing funds use equity × 1.50.",
                        "execution_intent": "broker_paper",
                        "current_user": SimpleNamespace(
                            tenant_id=tenant.id,
                            tenant_slug="alpha-desk",
                            user_id="demo-trader",
                        ),
                    },
                ),
                patch.object(
                    trade_automation_service.risk_control_service,
                    "compute_current_equity",
                    return_value={"current_equity_estimate": 100000.0},
                ) as compute_equity_mock,
                patch.object(
                    trade_automation_service.risk_control_service,
                    "update_high_water_runtime",
                    return_value={"current_equity_estimate": 100000.0, "drawdown_pct": 0.0},
                ) as update_high_water_mock,
            ):
                trade_automation_service._run_trade_automation_cycle(
                    db,
                    tenant=tenant,
                    state=state,
                    forced=False,
                    actor=None,
                )

        self.assertEqual(compute_equity_mock.call_args.kwargs["account_size"], 100000.0)
        self.assertEqual(update_high_water_mock.call_args.kwargs["starting_equity"], 100000.0)

    def test_trade_automation_cycle_stands_down_on_daily_loss_lock(self) -> None:
        from backend.core.database import Base
        from backend.services import tenant_service, trade_automation_service

        engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
        Session = sessionmaker(bind=engine, expire_on_commit=False, future=True)
        Base.metadata.create_all(bind=engine)

        fake_settings = SimpleNamespace(
            demo_tenant_slug="alpha-desk",
            demo_tenant_name="Alpha Desk",
            demo_tenant_plan="pro",
        )
        with Session() as db, patch.object(tenant_service, "settings", fake_settings):
            identity = tenant_service.ensure_demo_tenant_for_user(
                db,
                auth_subject="demo-trader",
                email="demo@example.test",
                name="Demo Trader",
                provider="local-demo",
                requested_tenant_slug="alpha-desk",
            )
            tenant = identity["active_tenant"]
            state = trade_automation_service._read_trade_automation_state(tenant)
            state["settings"].update(
                {
                    "enabled": True,
                    "armed": True,
                    "account_size": 10000.0,
                    "risk_percent": 0.25,
                    "max_daily_loss_r": 1.0,
                }
            )
            automation_closed = pd.DataFrame(
                [
                    {
                        "trade_id": "auto-loss-1",
                        "ticker": "SPY",
                        "automation_origin": "trade_automation",
                        "automation_tenant_id": tenant.id,
                        "automation_profile_key": "personal_paper",
                        "realized_pnl": -30.0,
                        "closed_at": "2026-04-20T14:45:00+00:00",
                    }
                ]
            )

            with (
                patch.object(trade_automation_service, "_utc_now", return_value=datetime(2026, 4, 20, 15, 0, tzinfo=timezone.utc)),
                patch.object(trade_automation_service, "get_trade_summary", return_value={"rollout_readiness": {}}),
                patch.object(trade_automation_service, "sync_pending_orders_from_broker", return_value=None),
                patch.object(
                    trade_automation_service,
                    "_resolve_trade_automation_profile_account_context",
                    return_value={
                        "profile_key": "personal_paper",
                        "scope": "personal_paper",
                        "linked_account": None,
                        "account_summary": {"equity": 10000.0, "cash": 10000.0},
                        "effective_funds": 10000.0,
                        "funds_source": "equity",
                        "execution_intent": "broker_paper",
                        "current_user": SimpleNamespace(
                            tenant_id=tenant.id,
                            tenant_slug="alpha-desk",
                            auth_subject="demo-trader",
                            user_id="demo-trader",
                        ),
                    },
                ),
                patch.object(trade_automation_service, "build_watchlist", side_effect=AssertionError("watchlist should not run after a daily loss lock")),
                patch.object(trade_automation_service.sdm, "read_open_trades", return_value=pd.DataFrame()),
                patch.object(trade_automation_service.sdm, "read_pending_orders", return_value=pd.DataFrame()),
                patch.object(trade_automation_service.sdm, "read_closed_trades", return_value=automation_closed),
            ):
                snapshot = trade_automation_service._run_trade_automation_cycle(db, tenant=tenant, state=state, forced=False, actor=None)

        self.assertEqual(snapshot["guardrails"]["status"]["reason"], "daily_loss_lock")
        self.assertEqual(snapshot["runtime"]["last_rejection"]["reason"], "daily_loss_lock")
        self.assertEqual(snapshot["history"][0]["reason"], "daily_loss_lock")

    def test_trade_automation_candidate_selection_requires_valid_trade(self) -> None:
        from backend.services import trade_automation_service

        state = {
            "settings": {
                "tickers": ["SPY", "QQQ"],
                "interval": "5m",
                "horizon": 5,
                "max_open_positions": 1,
                "use_fast_model": True,
                "long_only": True,
                "instrument_type": "equity",
            }
        }
        watchlist = {
            "rows": [
                {
                    "ticker": "SPY",
                    "trade_decision": "PASS",
                    "verdict": "BULLISH",
                    "ranking_tier": "review",
                    "ranking_score": 82.0,
                    "event_risk": False,
                }
            ]
        }

        with (
            patch.object(trade_automation_service, "build_watchlist", return_value=watchlist),
            patch.object(trade_automation_service, "_is_ticker_on_cooldown", return_value=False),
        ):
            candidate, _ = trade_automation_service._select_candidate_from_watchlist(state)

        self.assertIsNone(candidate)

    def test_trade_automation_candidate_selection_skips_bearish_when_long_only(self) -> None:
        from backend.services import trade_automation_service

        state = {
            "settings": {
                "tickers": ["SPY", "QQQ"],
                "interval": "5m",
                "horizon": 5,
                "max_open_positions": 1,
                "use_fast_model": True,
                "long_only": True,
                "instrument_type": "equity",
            }
        }
        watchlist = {
            "rows": [
                {
                    "ticker": "SPY",
                    "trade_decision": "VALID TRADE",
                    "verdict": "BEARISH",
                    "ranking_tier": "ready",
                    "ranking_score": 88.0,
                    "event_risk": False,
                },
                {
                    "ticker": "QQQ",
                    "trade_decision": "VALID TRADE",
                    "verdict": "BULLISH",
                    "ranking_tier": "ready",
                    "ranking_score": 79.0,
                    "event_risk": False,
                },
            ]
        }

        with (
            patch.object(trade_automation_service, "build_watchlist", return_value=watchlist),
            patch.object(trade_automation_service, "_is_ticker_on_cooldown", return_value=False),
        ):
            candidate, _ = trade_automation_service._select_candidate_from_watchlist(state)

        self.assertIsNotNone(candidate)
        self.assertEqual(candidate["ticker"], "QQQ")

    def test_trade_automation_candidate_selection_supports_parallel_equity_and_option_paths(self) -> None:
        from backend.services import trade_automation_service

        state = {
            "settings": {
                "tickers": ["SPY", "QQQ", "AAPL"],
                "interval": "5m",
                "horizon": 5,
                "max_open_positions": 2,
                "use_fast_model": True,
                "long_only": True,
                "auto_trade_equities": True,
                "auto_trade_listed_options": True,
            }
        }
        watchlist = {
            "rows": [
                {
                    "ticker": "SPY",
                    "trade_decision": "VALID TRADE",
                    "verdict": "BEARISH",
                    "ranking_tier": "ready",
                    "ranking_score": 91.0,
                    "event_risk": False,
                    "direction": "put",
                    "contract_symbol": "SPY260417P00560000",
                },
                {
                    "ticker": "QQQ",
                    "trade_decision": "VALID TRADE",
                    "verdict": "BULLISH",
                    "ranking_tier": "ready",
                    "ranking_score": 87.0,
                    "event_risk": False,
                },
            ]
        }

        with (
            patch.object(trade_automation_service, "build_watchlist", return_value=watchlist),
            patch.object(trade_automation_service, "_is_ticker_on_cooldown", return_value=False),
        ):
            candidates, _ = trade_automation_service._select_candidates_from_watchlist(state)

        self.assertEqual(len(candidates), 2)
        self.assertEqual(candidates[0]["ticker"], "SPY")
        self.assertEqual(candidates[0]["automation_instrument_type"], "listed_option")
        self.assertEqual(candidates[1]["ticker"], "QQQ")
        self.assertEqual(candidates[1]["automation_instrument_type"], "equity")

    def test_trade_automation_candidate_ranking_uses_profile_funds_for_projected_notional(self) -> None:
        from backend.services import trade_automation_service

        state = {
            "settings": {
                "tickers": ["SPY"],
                "interval": "5m",
                "horizon": 5,
                "max_open_positions": 2,
                "use_fast_model": True,
                "long_only": True,
                "instrument_type": "equity",
                "risk_percent": 1.0,
                "account_size": 100000.0,
            }
        }
        rows = [
            {
                "ticker": "SPY",
                "trade_decision": "VALID TRADE",
                "verdict": "BULLISH",
                "ranking_tier": "ready",
                "ranking_score": 82.0,
                "event_risk": False,
            }
        ]

        with patch.object(trade_automation_service, "_is_ticker_on_cooldown", return_value=False):
            ranked = trade_automation_service._rank_automation_candidates(
                state=state,
                rows=rows,
                now=datetime(2026, 4, 23, 14, 0, tzinfo=timezone.utc),
                current_equity=5000.0,
            )

        self.assertEqual(len(ranked), 1)
        self.assertEqual(ranked[0]["ticker"], "SPY")
        self.assertEqual(ranked[0]["projected_position_cost"], 500.0)

    def test_trade_automation_equity_path_survives_when_option_path_is_weak(self) -> None:
        from backend.services import trade_automation_service

        state = {
            "settings": {
                "tickers": ["SPY"],
                "interval": "5m",
                "horizon": 5,
                "max_open_positions": 2,
                "use_fast_model": True,
                "long_only": True,
                "auto_trade_equities": True,
                "auto_trade_listed_options": True,
            }
        }
        watchlist = {
            "rows": [
                {
                    "ticker": "SPY",
                    "trade_decision": "VALID TRADE",
                    "verdict": "BULLISH",
                    "ranking_tier": "promote",
                    "ranking_score": 88.0,
                    "execution_score": 78.0,
                    "portfolio_score": 80.0,
                    "event_risk": False,
                    "vehicle_recommendation": "equity",
                    "vehicle_reason": "Signal is promotable, but the option chain is weaker than stock execution.",
                    "option_execution_profile": {
                        "execution_score": 44.0,
                        "contract_quality_tier": "weak",
                    },
                    "contract_symbol": "SPY260515C00520000",
                }
            ]
        }

        with (
            patch.object(trade_automation_service, "build_watchlist", return_value=watchlist),
            patch.object(trade_automation_service, "_is_ticker_on_cooldown", return_value=False),
        ):
            candidates, _ = trade_automation_service._select_candidates_from_watchlist(state)

        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0]["ticker"], "SPY")
        self.assertEqual(candidates[0]["automation_instrument_type"], "equity")

    def test_trade_automation_prevents_duplicate_underlying_across_paths(self) -> None:
        from backend.services import trade_automation_service

        state = {
            "settings": {
                "tickers": ["SPY"],
                "interval": "5m",
                "horizon": 5,
                "max_open_positions": 2,
                "use_fast_model": True,
                "long_only": True,
                "auto_trade_equities": True,
                "auto_trade_listed_options": True,
            }
        }
        watchlist = {
            "rows": [
                {
                    "ticker": "SPY",
                    "trade_decision": "VALID TRADE",
                    "verdict": "BULLISH",
                    "ranking_tier": "promote",
                    "ranking_score": 90.0,
                    "execution_score": 82.0,
                    "portfolio_score": 84.0,
                    "event_risk": False,
                    "direction": "call",
                    "contract_symbol": "SPY260515C00520000",
                }
            ]
        }

        with (
            patch.object(trade_automation_service, "build_watchlist", return_value=watchlist),
            patch.object(trade_automation_service, "_is_ticker_on_cooldown", return_value=False),
        ):
            candidates, watchlist_payload = trade_automation_service._select_candidates_from_watchlist(state)

        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0]["ticker"], "SPY")
        self.assertEqual(len(watchlist_payload["path_evaluations"]), 2)

    def test_trade_automation_candidate_selection_rejects_high_alpha_poor_execution(self) -> None:
        from backend.services import trade_automation_service

        state = {
            "settings": {
                "tickers": ["SPY", "QQQ"],
                "interval": "5m",
                "horizon": 5,
                "max_open_positions": 2,
                "cycle_entry_rank_limit": 2,
                "use_fast_model": True,
                "long_only": True,
                "instrument_type": "equity",
                "account_size": 10000.0,
                "risk_percent": 0.5,
            }
        }
        watchlist = {
            "rows": [
                {
                    "ticker": "SPY",
                    "trade_decision": "VALID TRADE",
                    "verdict": "BULLISH",
                    "ranking_tier": "promote",
                    "ranking_score": 86.0,
                    "alpha_score": 94.0,
                    "execution_score": 42.0,
                    "portfolio_score": 63.0,
                    "event_risk": False,
                },
                {
                    "ticker": "QQQ",
                    "trade_decision": "VALID TRADE",
                    "verdict": "BULLISH",
                    "ranking_tier": "promote",
                    "ranking_score": 82.0,
                    "alpha_score": 76.0,
                    "execution_score": 78.0,
                    "portfolio_score": 79.0,
                    "event_risk": False,
                },
            ]
        }

        with (
            patch.object(trade_automation_service, "build_watchlist", return_value=watchlist),
            patch.object(trade_automation_service, "_is_ticker_on_cooldown", return_value=False),
        ):
            candidates, _ = trade_automation_service._select_candidates_from_watchlist(state)

        self.assertEqual([item["ticker"] for item in candidates], ["QQQ"])

    def test_trade_automation_candidate_selection_rejects_good_execution_weak_alpha(self) -> None:
        from backend.services import trade_automation_service

        state = {
            "settings": {
                "tickers": ["SPY", "QQQ"],
                "interval": "5m",
                "horizon": 5,
                "max_open_positions": 2,
                "cycle_entry_rank_limit": 2,
                "use_fast_model": True,
                "long_only": True,
                "instrument_type": "equity",
                "account_size": 10000.0,
                "risk_percent": 0.5,
            }
        }
        watchlist = {
            "rows": [
                {
                    "ticker": "SPY",
                    "trade_decision": "VALID TRADE",
                    "verdict": "BULLISH",
                    "ranking_tier": "promote",
                    "ranking_score": 74.0,
                    "alpha_score": 41.0,
                    "execution_score": 89.0,
                    "portfolio_score": 58.0,
                    "event_risk": False,
                }
            ]
        }

        with (
            patch.object(trade_automation_service, "build_watchlist", return_value=watchlist),
            patch.object(trade_automation_service, "_is_ticker_on_cooldown", return_value=False),
        ):
            candidates, _ = trade_automation_service._select_candidates_from_watchlist(state)

        self.assertEqual(candidates, [])

    def test_trade_automation_candidate_selection_limits_rank_to_top_two(self) -> None:
        from backend.services import trade_automation_service

        state = {
            "settings": {
                "tickers": ["SPY", "NVDA", "JPM"],
                "interval": "5m",
                "horizon": 5,
                "max_open_positions": 4,
                "cycle_entry_rank_limit": 2,
                "use_fast_model": True,
                "long_only": True,
                "instrument_type": "equity",
                "account_size": 10000.0,
                "risk_percent": 0.5,
            }
        }
        watchlist = {
            "rows": [
                {"ticker": "SPY", "trade_decision": "VALID TRADE", "verdict": "BULLISH", "ranking_tier": "promote", "portfolio_score": 88.0, "execution_score": 82.0, "event_risk": False, "proxy_correlation_bucket": "broad_market"},
                {"ticker": "NVDA", "trade_decision": "VALID TRADE", "verdict": "BULLISH", "ranking_tier": "promote", "portfolio_score": 84.0, "execution_score": 80.0, "event_risk": False, "proxy_correlation_bucket": "semiconductors"},
                {"ticker": "JPM", "trade_decision": "VALID TRADE", "verdict": "BULLISH", "ranking_tier": "promote", "portfolio_score": 79.0, "execution_score": 76.0, "event_risk": False, "proxy_correlation_bucket": "banks"},
            ]
        }

        with (
            patch.object(trade_automation_service, "build_watchlist", return_value=watchlist),
            patch.object(trade_automation_service, "_is_ticker_on_cooldown", return_value=False),
        ):
            candidates, _ = trade_automation_service._select_candidates_from_watchlist(state)

        self.assertEqual([item["ticker"] for item in candidates], ["SPY", "NVDA"])
        self.assertEqual([item["portfolio_rank"] for item in candidates], [1, 2])

    def test_trade_automation_candidate_selection_blocks_second_bucket_entry_when_bucket_is_crowded(self) -> None:
        from backend.services import trade_automation_service

        state = {
            "settings": {
                "tickers": ["SPY", "QQQ", "JPM"],
                "interval": "5m",
                "horizon": 5,
                "max_open_positions": 3,
                "cycle_entry_rank_limit": 2,
                "use_fast_model": True,
                "long_only": True,
                "instrument_type": "equity",
                "account_size": 10000.0,
                "risk_percent": 0.5,
            }
        }
        watchlist = {
            "rows": [
                {"ticker": "SPY", "trade_decision": "VALID TRADE", "verdict": "BULLISH", "ranking_tier": "promote", "portfolio_score": 90.0, "execution_score": 86.0, "event_risk": False, "proxy_correlation_bucket": "broad_market", "projected_position_cost": 1500.0},
                {"ticker": "QQQ", "trade_decision": "VALID TRADE", "verdict": "BULLISH", "ranking_tier": "promote", "portfolio_score": 87.0, "execution_score": 83.0, "event_risk": False, "proxy_correlation_bucket": "broad_market", "projected_position_cost": 1500.0},
                {"ticker": "JPM", "trade_decision": "VALID TRADE", "verdict": "BULLISH", "ranking_tier": "promote", "portfolio_score": 81.0, "execution_score": 75.0, "event_risk": False, "proxy_correlation_bucket": "banks", "projected_position_cost": 1000.0},
            ]
        }

        with (
            patch.object(trade_automation_service, "build_watchlist", return_value=watchlist),
            patch.object(trade_automation_service, "_is_ticker_on_cooldown", return_value=False),
        ):
            candidates, _ = trade_automation_service._select_candidates_from_watchlist(state)

        self.assertEqual([item["ticker"] for item in candidates], ["SPY", "JPM"])

    def test_trade_automation_cycle_allows_winner_only_pyramid_once(self) -> None:
        from backend.core.database import Base
        from backend.services import tenant_service, trade_automation_service

        engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
        Session = sessionmaker(bind=engine, expire_on_commit=False, future=True)
        Base.metadata.create_all(bind=engine)

        fake_settings = SimpleNamespace(
            demo_tenant_slug="alpha-desk",
            demo_tenant_name="Alpha Desk",
            demo_tenant_plan="pro",
        )
        with Session() as db, patch.object(tenant_service, "settings", fake_settings):
            identity = tenant_service.ensure_demo_tenant_for_user(
                db,
                auth_subject="demo-trader",
                email="demo@example.test",
                name="Demo Trader",
                provider="local-demo",
                requested_tenant_slug="alpha-desk",
            )
            tenant = identity["active_tenant"]
            state = trade_automation_service._read_trade_automation_state(tenant)
            state["settings"].update(
                {
                    "enabled": True,
                    "armed": True,
                    "tickers": ["AAPL"],
                    "account_size": 10000.0,
                    "risk_percent": 0.5,
                    "max_open_positions": 4,
                    "max_notional_per_trade": 1500.0,
                    "max_total_open_notional": 15000.0,
                    "allow_pyramiding": True,
                    "require_liquidity_fields": True,
                    "min_edge_to_cost_ratio": 2.5,
                }
            )
            watchlist = {
                "rows": [
                    {
                        "ticker": "AAPL",
                        "trade_decision": "VALID TRADE",
                        "verdict": "BULLISH",
                        "ranking_tier": "promote",
                        "portfolio_score": 88.0,
                        "execution_score": 82.0,
                        "event_risk": False,
                        "proxy_correlation_bucket": "mega_cap_tech",
                        "projected_position_cost": 1200.0,
                        "live_price": 105.0,
                        "close": 105.0,
                        "spread_bps": 8.0,
                        "average_dollar_volume": 5_000_000_000.0,
                        "average_1m_dollar_volume": 2_000_000.0,
                        "edge_to_cost_ratio": 3.4,
                    }
                ]
            }
            owned_open = pd.DataFrame(
                [
                    {
                        "ticker": "AAPL",
                        "instrument_type": "equity",
                        "live_price_at_open": 100.0,
                        "verdict": "BULLISH",
                        "position_cost": 1000.0,
                        "automation_origin": "trade_automation",
                        "automation_tenant_id": tenant.id,
                    }
                ]
            )
            pending_stub = {"order_id": "pending-1", "trade_id": "trade-1"}
            open_result = {"position_opened": False, "record": {}, "pending_order": pending_stub, "execution": {"status": "accepted"}}

            with (
                patch.object(trade_automation_service, "_utc_now", return_value=datetime(2026, 4, 20, 15, 0, tzinfo=timezone.utc)),
                patch.object(trade_automation_service, "get_trade_summary", return_value={"rollout_readiness": {}}),
                patch.object(trade_automation_service, "sync_pending_orders_from_broker", return_value=None),
                patch.object(trade_automation_service, "_manage_automation_positions", return_value={"acted_count": 0, "failed_count": 0, "items": [], "failed_items": []}),
                patch.object(trade_automation_service, "_persist_watchlist_validation_snapshot", return_value=None),
                patch.object(trade_automation_service, "build_watchlist", return_value=watchlist),
                patch.object(trade_automation_service.sdm, "read_open_trades", return_value=owned_open),
                patch.object(trade_automation_service.sdm, "read_pending_orders", return_value=pd.DataFrame()),
                patch.object(trade_automation_service.sdm, "read_closed_trades", return_value=pd.DataFrame()),
                patch.object(trade_automation_service, "open_trade_from_request", return_value=open_result) as open_mock,
                patch.object(trade_automation_service.sdm, "update_pending_order", return_value=None) as update_pending_mock,
            ):
                snapshot = trade_automation_service._run_trade_automation_cycle(db, tenant=tenant, state=state, forced=False, actor=None)

        self.assertEqual(snapshot["runtime"]["last_decision"]["decision"], "opened")
        self.assertEqual(open_mock.call_count, 1)
        request = open_mock.call_args.args[0]
        self.assertEqual(request.route_family, "current")
        self.assertEqual(request.route_version, "ranked_entry_v1")
        self.assertEqual(request.automation_entry_reason, "ranked_candidate")
        self.assertEqual(request.thesis_direction, "BULLISH")
        self.assertEqual(update_pending_mock.call_args[0][1]["automation_pyramid_leg"], 2)

    def test_trade_automation_cycle_runs_linked_account_profile_with_linked_funds(self) -> None:
        from backend.core.database import Base
        from backend.models.saas import BrokerageLinkedAccount, User
        from backend.services import tenant_service, trade_automation_service

        engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
        Session = sessionmaker(bind=engine, expire_on_commit=False, future=True)
        Base.metadata.create_all(bind=engine)

        fake_settings = SimpleNamespace(
            demo_tenant_slug="alpha-desk",
            demo_tenant_name="Alpha Desk",
            demo_tenant_plan="pro",
        )
        fixed_now = datetime(2026, 4, 23, 15, 0, tzinfo=timezone.utc)

        with Session() as db, patch.object(tenant_service, "settings", fake_settings):
            identity = tenant_service.ensure_demo_tenant_for_user(
                db,
                auth_subject="demo-trader",
                email="demo@example.test",
                name="Demo Trader",
                provider="local-demo",
                requested_tenant_slug="alpha-desk",
            )
            actor = db.execute(select(User).where(User.auth_subject == "demo-trader")).scalar_one()
            tenant = identity["active_tenant"]
            linked_account = BrokerageLinkedAccount(
                tenant=tenant,
                owner_user=actor,
                provider="alpaca",
                label="Client Paper",
                account_environment="paper",
                connection_status="connected",
                token_health="healthy",
                approval_policy="approval_required",
                oauth_access_token="oauth-token",
                metadata_json={
                    "automation": {
                        "client_auto_trading_opt_in": True,
                        "operator_auto_trading_enabled": True,
                        "account_size": 25000.0,
                        "risk_percent": 0.75,
                        "max_notional_per_trade": 1800.0,
                        "max_open_positions": 2,
                    }
                },
            )
            db.add(linked_account)
            db.commit()

            profile_key = f"linked:{linked_account.id}"
            state = trade_automation_service._read_trade_automation_state(
                tenant,
                profile_key=profile_key,
                linked_account=linked_account,
            )
            state["settings"].update(
                {
                    "enabled": True,
                    "armed": True,
                    "tickers": ["AAPL"],
                    "account_size": 10000.0,
                    "risk_percent": 0.5,
                    "max_open_positions": 2,
                    "max_notional_per_trade": 1500.0,
                    "max_total_open_notional": 15000.0,
                    "allow_pyramiding": True,
                    "require_liquidity_fields": True,
                    "min_edge_to_cost_ratio": 2.5,
                    "cycle_entry_rank_limit": 2,
                    "instrument_type": "equity",
                }
            )
            watchlist = {
                "rows": [
                    {
                        "ticker": "AAPL",
                        "trade_decision": "VALID TRADE",
                        "verdict": "BULLISH",
                        "ranking_tier": "promote",
                        "portfolio_score": 88.0,
                        "execution_score": 82.0,
                        "event_risk": False,
                        "proxy_correlation_bucket": "mega_cap_tech",
                        "projected_position_cost": 1200.0,
                        "live_price": 105.0,
                        "close": 105.0,
                        "spread_bps": 8.0,
                        "average_dollar_volume": 5_000_000_000.0,
                        "average_1m_dollar_volume": 2_000_000.0,
                        "edge_to_cost_ratio": 3.4,
                    }
                ]
            }
            open_result_personal = {
                "position_opened": True,
                "record": {"trade_id": "trade-1", "order_id": "order-1"},
                "pending_order": {},
                "execution": {"status": "accepted"},
            }

            def _open_trade_side_effect(request, **_kwargs):
                return open_result_personal

            with (
                patch.object(trade_automation_service, "_utc_now", return_value=fixed_now),
                patch.object(trade_automation_service, "get_trade_summary", return_value={"rollout_readiness": {}, "client_automation": {}}),
                patch.object(trade_automation_service, "sync_pending_orders_from_broker", return_value=None),
                patch.object(
                    trade_automation_service,
                    "_resolve_trade_automation_profile_account_context",
                    return_value={
                        "profile_key": profile_key,
                        "scope": "linked",
                        "linked_account": linked_account,
                        "account_summary": {"equity": 25000.0, "cash": 25000.0},
                        "effective_funds": 25000.0,
                        "funds_source": "equity",
                        "execution_intent": "broker_paper",
                        "current_user": SimpleNamespace(
                            tenant_id=tenant.id,
                            tenant_slug="alpha-desk",
                            auth_subject="demo-trader",
                            user_id=str(actor.id),
                        ),
                    },
                ),
                patch.object(trade_automation_service, "_manage_automation_positions", return_value={"acted_count": 0, "failed_count": 0, "items": [], "failed_items": []}),
                patch.object(trade_automation_service, "_persist_watchlist_validation_snapshot", return_value=None),
                patch.object(trade_automation_service, "build_watchlist", return_value=watchlist),
                patch.object(trade_automation_service.sdm, "read_open_trades", return_value=pd.DataFrame()),
                patch.object(trade_automation_service.sdm, "read_pending_orders", return_value=pd.DataFrame()),
                patch.object(trade_automation_service.sdm, "read_closed_trades", return_value=pd.DataFrame()),
                patch.object(trade_automation_service.sdm, "update_open_trade", return_value={"trade_id": "trade-1", "order_id": "order-1"}),
                patch.object(trade_automation_service, "record_trade_automation_equity_snapshot", return_value={"cycle_id": "cycle-1", "cycle_at": fixed_now.isoformat()}),
                patch.object(trade_automation_service, "_build_live_current_route_collection_metrics", return_value={"current_route_fill_count": 0, "current_route_closed_trade_count": 0, "current_route_mismatched_count": 0, "current_route_sample_status": "insufficient", "mark_to_market_coverage_status": "missing", "ledger_snapshot_consistency": "unavailable", "metrics_source": "event_ledger", "current_route_validation_integrity": {}}),
                patch.object(trade_automation_service, "open_trade_from_request", side_effect=_open_trade_side_effect) as open_mock,
            ):
                snapshot = trade_automation_service._run_trade_automation_cycle(
                    db,
                    tenant=tenant,
                    state=state,
                    profile_key=profile_key,
                    linked_account=linked_account,
                    forced=False,
                    actor=actor,
                )

        self.assertEqual(open_mock.call_count, 1)
        request = open_mock.call_args.args[0]
        self.assertEqual(request.account_target_type, "linked_client")
        self.assertEqual(request.execution_mode, "automated_entry")
        self.assertEqual(request.linked_account_id, linked_account.id)
        self.assertEqual(request.account_size, 25000.0)
        self.assertEqual(request.risk_percent, 0.5)
        self.assertEqual(request.max_notional_per_trade, 1500.0)
        self.assertEqual(snapshot["profile_key"], profile_key)

    def test_trade_automation_cycle_blocks_averaging_down(self) -> None:
        from backend.core.database import Base
        from backend.services import tenant_service, trade_automation_service

        engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
        Session = sessionmaker(bind=engine, expire_on_commit=False, future=True)
        Base.metadata.create_all(bind=engine)

        fake_settings = SimpleNamespace(
            demo_tenant_slug="alpha-desk",
            demo_tenant_name="Alpha Desk",
            demo_tenant_plan="pro",
        )
        with Session() as db, patch.object(tenant_service, "settings", fake_settings):
            identity = tenant_service.ensure_demo_tenant_for_user(
                db,
                auth_subject="demo-trader",
                email="demo@example.test",
                name="Demo Trader",
                provider="local-demo",
                requested_tenant_slug="alpha-desk",
            )
            tenant = identity["active_tenant"]
            state = trade_automation_service._read_trade_automation_state(tenant)
            state["settings"].update(
                {
                    "enabled": True,
                    "armed": True,
                    "tickers": ["AAPL"],
                    "account_size": 10000.0,
                    "risk_percent": 0.5,
                    "max_open_positions": 4,
                    "max_notional_per_trade": 1500.0,
                    "max_total_open_notional": 15000.0,
                    "allow_pyramiding": True,
                    "require_liquidity_fields": True,
                    "min_edge_to_cost_ratio": 2.5,
                }
            )
            watchlist = {
                "rows": [
                    {
                        "ticker": "AAPL",
                        "trade_decision": "VALID TRADE",
                        "verdict": "BULLISH",
                        "ranking_tier": "promote",
                        "portfolio_score": 88.0,
                        "execution_score": 82.0,
                        "event_risk": False,
                        "proxy_correlation_bucket": "mega_cap_tech",
                        "projected_position_cost": 1200.0,
                        "live_price": 95.0,
                        "close": 95.0,
                        "spread_bps": 8.0,
                        "average_dollar_volume": 5_000_000_000.0,
                        "average_1m_dollar_volume": 2_000_000.0,
                        "edge_to_cost_ratio": 3.4,
                    }
                ]
            }
            owned_open = pd.DataFrame(
                [
                    {
                        "ticker": "AAPL",
                        "instrument_type": "equity",
                        "live_price_at_open": 100.0,
                        "verdict": "BULLISH",
                        "position_cost": 1000.0,
                        "automation_origin": "trade_automation",
                        "automation_tenant_id": tenant.id,
                    }
                ]
            )

            with (
                patch.object(trade_automation_service, "_utc_now", return_value=datetime(2026, 4, 20, 15, 0, tzinfo=timezone.utc)),
                patch.object(trade_automation_service, "get_trade_summary", return_value={"rollout_readiness": {}}),
                patch.object(trade_automation_service, "sync_pending_orders_from_broker", return_value=None),
                patch.object(trade_automation_service, "_manage_automation_positions", return_value={"acted_count": 0, "failed_count": 0, "items": [], "failed_items": []}),
                patch.object(trade_automation_service, "_persist_watchlist_validation_snapshot", return_value=None),
                patch.object(trade_automation_service, "build_watchlist", return_value=watchlist),
                patch.object(trade_automation_service.sdm, "read_open_trades", return_value=owned_open),
                patch.object(trade_automation_service.sdm, "read_pending_orders", return_value=pd.DataFrame()),
                patch.object(trade_automation_service.sdm, "read_closed_trades", return_value=pd.DataFrame()),
                patch.object(trade_automation_service, "open_trade_from_request", side_effect=AssertionError("should not open")),
            ):
                snapshot = trade_automation_service._run_trade_automation_cycle(db, tenant=tenant, state=state, forced=False, actor=None)

        self.assertEqual(snapshot["runtime"]["last_rejection"]["reason"], "averaging_down_blocked")

    def test_trade_automation_snapshot_surfaces_ranked_entry_controls_and_runtime_telemetry(self) -> None:
        from backend.core.database import Base
        from backend.services import tenant_service, trade_automation_service

        engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
        Session = sessionmaker(bind=engine, expire_on_commit=False, future=True)
        Base.metadata.create_all(bind=engine)

        fake_settings = SimpleNamespace(
            demo_tenant_slug="alpha-desk",
            demo_tenant_name="Alpha Desk",
            demo_tenant_plan="pro",
        )
        with Session() as db, patch.object(tenant_service, "settings", fake_settings):
            identity = tenant_service.ensure_demo_tenant_for_user(
                db,
                auth_subject="demo-trader",
                email="demo@example.test",
                name="Demo Trader",
                provider="local-demo",
                requested_tenant_slug="alpha-desk",
            )
            tenant = identity["active_tenant"]
            state = trade_automation_service._read_trade_automation_state(tenant)
            state["settings"].update(
                {
                    "enabled": True,
                    "armed": True,
                    "max_gross_leverage": 1.5,
                    "max_single_position_pct": 12.0,
                    "max_correlated_bucket_pct": 35.0,
                    "min_edge_to_cost_ratio": 2.5,
                    "allow_pyramiding": True,
                    "require_liquidity_fields": True,
                    "cycle_entry_rank_limit": 2,
                }
            )
            state["runtime"].update(
                {
                    "last_candidate": {
                        "ticker": "NVDA",
                        "alpha_score": 92.0,
                        "execution_score": 78.0,
                        "portfolio_score": 84.0,
                        "edge_to_cost_ratio": 3.6,
                        "portfolio_rank": 1,
                        "proxy_correlation_bucket": "semiconductors",
                        "auto_entry_eligible": True,
                    },
                    "last_rejection": {
                        "reason": "correlated_bucket_cap",
                        "detail": "Semiconductor exposure already reached the configured bucket cap.",
                        "ticker": "AMD",
                    },
                    "last_path_evaluations": [
                        {"instrument_type": "equity", "status": "eligible", "ticker": "NVDA", "detail": "Top-ranked equity candidate."},
                        {"instrument_type": "listed_option", "status": "blocked", "ticker": "AMD", "detail": "Option path failed liquidity review."},
                    ],
                    "last_collection_blocker": "liquidity_blocked",
                    "current_route_reconciliation_status": "issues_present",
                    "current_route_orphan_order_event_count": 2,
                    "last_submitted_current_route_order_at": "2026-04-19T14:31:00+00:00",
                    "last_current_route_fill_at": "2026-04-19T14:32:00+00:00",
                    "last_current_route_close_at": "2026-04-19T14:37:00+00:00",
                }
            )
            trade_automation_service._write_trade_automation_state(tenant, state)
            db.commit()
            current_user = SimpleNamespace(
                tenant_id=tenant.id,
                tenant_slug="alpha-desk",
                tenant_name="Alpha Desk",
                auth_subject="demo-trader",
                user_id="demo-trader",
                permissions=("tenant.manage_support",),
            )

            with (
                patch.object(trade_automation_service, "get_trade_summary", return_value={"rollout_readiness": {"ranked_entry_rollout": {"status": "accepted"}}}),
                patch.object(trade_automation_service.sdm, "read_open_trades", return_value=pd.DataFrame()),
                patch.object(trade_automation_service.sdm, "read_pending_orders", return_value=pd.DataFrame()),
                patch.object(trade_automation_service.sdm, "read_closed_trades", return_value=pd.DataFrame()),
                patch.object(trade_automation_service.sdm, "monitor_open_trades", return_value=pd.DataFrame()),
                patch.object(trade_automation_service, "_build_trade_automation_performance_snapshot", return_value={"status": {"label": "Idle", "tone": "neutral"}, "cards": [], "metrics": {}, "recent_closed": [], "recent_events": []}),
                patch.object(trade_automation_service, "_build_trade_automation_guardrail_snapshot", return_value={"status": {"label": "Ready", "tone": "positive"}, "cards": [], "metrics": {}}),
                patch.object(trade_automation_service, "record_trade_automation_equity_snapshot", side_effect=AssertionError("snapshot reads should not write")),
                patch.object(trade_automation_service, "get_latest_trade_automation_equity_snapshot", return_value={"current_equity_estimate": 10000.0}),
            ):
                snapshot = trade_automation_service.get_tenant_trade_automation_snapshot(db, current_user=current_user)

        self.assertEqual(snapshot["settings"]["max_gross_leverage"], 1.5)
        self.assertEqual(snapshot["settings"]["max_single_position_pct"], 12.0)
        self.assertEqual(snapshot["settings"]["max_correlated_bucket_pct"], 35.0)
        self.assertEqual(snapshot["settings"]["min_edge_to_cost_ratio"], 2.5)
        self.assertTrue(snapshot["settings"]["allow_pyramiding"])
        self.assertTrue(snapshot["settings"]["require_liquidity_fields"])
        self.assertEqual(snapshot["settings"]["cycle_entry_rank_limit"], 2)
        self.assertEqual(snapshot["runtime"]["last_candidate"]["ticker"], "NVDA")
        self.assertEqual(snapshot["runtime"]["last_candidate"]["portfolio_rank"], 1)
        self.assertEqual(snapshot["runtime"]["last_rejection"]["reason"], "correlated_bucket_cap")
        self.assertEqual(len(snapshot["runtime"]["last_path_evaluations"]), 2)
        self.assertTrue(snapshot["collection_phase"]["collection_phase_active"])
        self.assertEqual(snapshot["collection_phase"]["collection_phase_label"], "Broker reconcile issue")
        self.assertEqual(snapshot["collection_phase"]["last_collection_blocker"], "broker_reconcile_failed")
        self.assertEqual(snapshot["collection_phase"]["current_route_reconciliation_status"], "issues_present")
        self.assertEqual(snapshot["collection_phase"]["current_route_orphan_order_event_count"], 2)
        self.assertTrue(snapshot["rollout_readiness"]["collection_phase_active"])

    def test_trade_automation_cycle_records_one_authoritative_equity_snapshot(self) -> None:
        from backend.core.database import Base
        from backend.services import tenant_service, trade_automation_service

        engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
        Session = sessionmaker(bind=engine, expire_on_commit=False, future=True)
        Base.metadata.create_all(bind=engine)

        fake_settings = SimpleNamespace(
            demo_tenant_slug="alpha-desk",
            demo_tenant_name="Alpha Desk",
            demo_tenant_plan="pro",
        )
        fixed_now = datetime(2026, 4, 20, 15, 0, tzinfo=timezone.utc)
        fixed_cycle_id = "cycle-fixed-1"

        with Session() as db, patch.object(tenant_service, "settings", fake_settings):
            identity = tenant_service.ensure_demo_tenant_for_user(
                db,
                auth_subject="demo-trader",
                email="demo@example.test",
                name="Demo Trader",
                provider="local-demo",
                requested_tenant_slug="alpha-desk",
            )
            tenant = identity["active_tenant"]
            state = trade_automation_service._read_trade_automation_state(tenant)
            state["settings"].update(
                {
                    "enabled": True,
                    "armed": True,
                    "tickers": ["SPY"],
                    "account_size": 10000.0,
                    "risk_percent": 0.5,
                    "regular_hours_only": True,
                }
            )

            with (
                patch.object(trade_automation_service, "_utc_now", return_value=fixed_now),
                patch.object(trade_automation_service, "uuid4", return_value=fixed_cycle_id),
                patch.object(trade_automation_service, "get_trade_summary", return_value={"rollout_readiness": {}}),
                patch.object(trade_automation_service, "sync_pending_orders_from_broker", return_value=None),
                patch.object(trade_automation_service, "_manage_automation_positions", return_value={"acted_count": 0, "failed_count": 0, "items": [], "failed_items": []}),
                patch.object(trade_automation_service, "_persist_watchlist_validation_snapshot", return_value=None),
                patch.object(trade_automation_service, "build_watchlist", return_value={"rows": [], "path_evaluations": []}),
                patch.object(trade_automation_service.sdm, "read_open_trades", return_value=pd.DataFrame()),
                patch.object(trade_automation_service.sdm, "read_pending_orders", return_value=pd.DataFrame()),
                patch.object(trade_automation_service.sdm, "read_closed_trades", return_value=pd.DataFrame()),
                patch.object(trade_automation_service.sdm, "monitor_open_trades", return_value=pd.DataFrame()),
                patch.object(trade_automation_service, "_build_trade_automation_performance_snapshot", return_value={"status": {"label": "Idle", "tone": "neutral"}, "cards": [], "metrics": {}, "recent_closed": [], "recent_events": []}),
                patch.object(trade_automation_service, "_build_trade_automation_guardrail_snapshot", return_value={"status": {"label": "Ready", "tone": "positive"}, "cards": [], "metrics": {}}),
                patch.object(trade_automation_service, "record_trade_automation_equity_snapshot", return_value={"cycle_id": fixed_cycle_id, "cycle_at": fixed_now.isoformat()}) as record_snapshot_mock,
            ):
                snapshot = trade_automation_service._run_trade_automation_cycle(db, tenant=tenant, state=state, forced=False, actor=None)

        self.assertEqual(record_snapshot_mock.call_count, 1)
        self.assertEqual(record_snapshot_mock.call_args.kwargs["cycle_id"], fixed_cycle_id)
        self.assertEqual(record_snapshot_mock.call_args.kwargs["cycle_at"], fixed_now)
        self.assertEqual(snapshot["equity_snapshot"]["cycle_id"], fixed_cycle_id)
        self.assertEqual(snapshot["equity_snapshot"]["cycle_at"], fixed_now.isoformat())

    def test_trade_automation_session_snapshot_allows_pre_and_after_hours_but_not_closed(self) -> None:
        from backend.services import trade_automation_service

        cases = [
            (datetime(2026, 4, 20, 12, 0, tzinfo=timezone.utc), "pre_market", True),
            (datetime(2026, 4, 20, 22, 0, tzinfo=timezone.utc), "after_hours", True),
            (datetime(2026, 4, 21, 1, 0, tzinfo=timezone.utc), "closed", False),
        ]

        for fixed_now, expected_mode, expected_entries in cases:
            with self.subTest(expected_mode=expected_mode), patch.object(trade_automation_service, "_utc_now", return_value=fixed_now):
                snapshot = trade_automation_service._build_session_snapshot(flatten_before_close_minutes=15)

            self.assertEqual(snapshot["session_mode"], expected_mode)
            self.assertEqual(snapshot["new_entries_allowed"], expected_entries)
            self.assertEqual(snapshot["extended_session"], expected_mode in {"pre_market", "after_hours"})

    def test_trade_automation_cycle_collection_phase_forces_broker_paper_and_session_flex_equities(self) -> None:
        from backend.core.database import Base
        from backend.services import tenant_service, trade_automation_service

        engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
        Session = sessionmaker(bind=engine, expire_on_commit=False, future=True)
        Base.metadata.create_all(bind=engine)

        fake_settings = SimpleNamespace(
            demo_tenant_slug="alpha-desk",
            demo_tenant_name="Alpha Desk",
            demo_tenant_plan="pro",
        )
        fixed_now = datetime(2026, 4, 20, 15, 0, tzinfo=timezone.utc)

        with Session() as db, patch.object(tenant_service, "settings", fake_settings):
            identity = tenant_service.ensure_demo_tenant_for_user(
                db,
                auth_subject="demo-trader",
                email="demo@example.test",
                name="Demo Trader",
                provider="local-demo",
                requested_tenant_slug="alpha-desk",
            )
            tenant = identity["active_tenant"]
            state = trade_automation_service._read_trade_automation_state(tenant)
            state["settings"].update(
                {
                    "enabled": True,
                    "armed": True,
                    "execution_intent": "broker_live",
                    "regular_hours_only": False,
                    "tickers": ["AAPL"],
                    "account_size": 10000.0,
                    "risk_percent": 0.5,
                    "max_open_positions": 2,
                    "max_notional_per_trade": 1500.0,
                    "max_total_open_notional": 15000.0,
                    "allow_pyramiding": True,
                    "require_liquidity_fields": True,
                    "min_edge_to_cost_ratio": 2.5,
                    "cycle_entry_rank_limit": 2,
                }
            )
            watchlist = {
                "rows": [
                    {
                        "ticker": "AAPL",
                        "trade_decision": "VALID TRADE",
                        "verdict": "BULLISH",
                        "ranking_tier": "promote",
                        "alpha_score": 91.0,
                        "execution_score": 82.0,
                        "portfolio_score": 88.0,
                        "portfolio_rank": 1,
                        "event_risk": False,
                        "proxy_correlation_bucket": "mega_cap_tech",
                        "projected_position_cost": 1200.0,
                        "live_price": 105.0,
                        "close": 105.0,
                        "spread_bps": 8.0,
                        "average_dollar_volume": 5_000_000_000.0,
                        "average_1m_dollar_volume": 2_000_000.0,
                        "edge_to_cost_ratio": 3.4,
                    }
                ]
            }

            with (
                patch.object(trade_automation_service, "_utc_now", return_value=fixed_now),
                patch.object(
                    trade_automation_service,
                    "get_trade_summary",
                    return_value={
                        "rollout_readiness": {
                            "status": "locked",
                            "label": "Paper first",
                            "allows_live_rollout": False,
                            "ranked_entry_rollout": {
                                "status": "partial",
                                "accepted": False,
                            },
                            "metrics": {
                                "current_route_fill_count": 4,
                                "current_route_closed_trade_count": 1,
                                "current_route_sample_status": "insufficient",
                                "mark_to_market_coverage_status": "missing",
                                "ledger_snapshot_consistency": "unavailable",
                            },
                        }
                    },
                ),
                patch.object(trade_automation_service, "sync_pending_orders_from_broker", return_value=None),
                patch.object(trade_automation_service, "_manage_automation_positions", return_value={"acted_count": 0, "failed_count": 0, "items": [], "failed_items": []}),
                patch.object(trade_automation_service, "_persist_watchlist_validation_snapshot", return_value=None),
                patch.object(trade_automation_service, "build_watchlist", return_value=watchlist),
                patch.object(trade_automation_service.sdm, "read_open_trades", return_value=pd.DataFrame()),
                patch.object(trade_automation_service.sdm, "read_pending_orders", return_value=pd.DataFrame()),
                patch.object(trade_automation_service.sdm, "read_closed_trades", return_value=pd.DataFrame()),
                patch.object(trade_automation_service.sdm, "monitor_open_trades", return_value=pd.DataFrame()),
                patch.object(
                    trade_automation_service.sdm,
                    "update_open_trade",
                    return_value={
                        "trade_id": "trade-1",
                        "order_id": "order-1",
                        "automation_origin": "trade_automation",
                    },
                ),
                patch.object(trade_automation_service, "record_trade_automation_equity_snapshot", return_value={"cycle_id": "cycle-1", "cycle_at": fixed_now.isoformat()}),
                patch.object(
                    trade_automation_service,
                    "_build_live_current_route_collection_metrics",
                    return_value={
                        "current_route_fill_count": 4,
                        "current_route_closed_trade_count": 1,
                        "current_route_mismatched_count": 0,
                        "current_route_sample_status": "insufficient",
                        "mark_to_market_coverage_status": "missing",
                        "ledger_snapshot_consistency": "unavailable",
                        "metrics_source": "event_ledger",
                        "route_window_start": None,
                        "route_window_end": None,
                        "route_window_snapshot_count": 0,
                        "current_route_latest_event_at": None,
                        "current_route_validation_integrity": {"status": "partial"},
                    },
                ),
                patch.object(trade_automation_service, "open_trade_from_request", return_value={"position_opened": True, "record": {"trade_id": "trade-1", "order_id": "order-1"}, "pending_order": {}, "execution": {"intent": "broker_paper"}}) as open_mock,
            ):
                snapshot = trade_automation_service._run_trade_automation_cycle(db, tenant=tenant, state=state, forced=False, actor=None)

        request = open_mock.call_args.args[0]
        self.assertEqual(request.execution_intent, "broker_paper")
        self.assertFalse(request.regular_hours_only)
        self.assertEqual(request.order_type, "limit")
        self.assertEqual(request.time_in_force, "day")
        self.assertFalse(request.extended_hours)
        self.assertEqual(snapshot["settings"]["execution_intent"], "broker_paper")
        self.assertFalse(snapshot["settings"]["regular_hours_only"])
        self.assertEqual(snapshot["settings"]["time_in_force"], "day_ext")
        self.assertTrue(snapshot["collection_phase"]["collection_phase_active"])
        self.assertEqual(snapshot["collection_phase"]["collection_phase_label"], "Collecting sample")

    def test_trade_automation_cycle_marks_no_candidates_collection_blocker(self) -> None:
        from backend.core.database import Base
        from backend.services import tenant_service, trade_automation_service

        engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
        Session = sessionmaker(bind=engine, expire_on_commit=False, future=True)
        Base.metadata.create_all(bind=engine)

        fake_settings = SimpleNamespace(
            demo_tenant_slug="alpha-desk",
            demo_tenant_name="Alpha Desk",
            demo_tenant_plan="pro",
        )
        fixed_now = datetime(2026, 4, 20, 15, 0, tzinfo=timezone.utc)

        with Session() as db, patch.object(tenant_service, "settings", fake_settings):
            identity = tenant_service.ensure_demo_tenant_for_user(
                db,
                auth_subject="demo-trader",
                email="demo@example.test",
                name="Demo Trader",
                provider="local-demo",
                requested_tenant_slug="alpha-desk",
            )
            tenant = identity["active_tenant"]
            state = trade_automation_service._read_trade_automation_state(tenant)
            state["settings"].update(
                {
                    "enabled": True,
                    "armed": True,
                    "execution_intent": "broker_live",
                    "tickers": ["AAPL"],
                    "account_size": 10000.0,
                    "risk_percent": 0.5,
                }
            )

            with (
                patch.object(trade_automation_service, "_utc_now", return_value=fixed_now),
                patch.object(
                    trade_automation_service,
                    "get_trade_summary",
                    return_value={"rollout_readiness": {"ranked_entry_rollout": {"accepted": False, "status": "partial"}}},
                ),
                patch.object(trade_automation_service, "sync_pending_orders_from_broker", return_value=None),
                patch.object(trade_automation_service, "_manage_automation_positions", return_value={"acted_count": 0, "failed_count": 0, "items": [], "failed_items": []}),
                patch.object(trade_automation_service, "_persist_watchlist_validation_snapshot", return_value=None),
                patch.object(trade_automation_service, "build_watchlist", return_value={"rows": [], "path_evaluations": []}),
                patch.object(trade_automation_service.sdm, "read_open_trades", return_value=pd.DataFrame()),
                patch.object(trade_automation_service.sdm, "read_pending_orders", return_value=pd.DataFrame()),
                patch.object(trade_automation_service.sdm, "read_closed_trades", return_value=pd.DataFrame()),
                patch.object(trade_automation_service.sdm, "monitor_open_trades", return_value=pd.DataFrame()),
                patch.object(trade_automation_service, "record_trade_automation_equity_snapshot", return_value={"cycle_id": "cycle-1", "cycle_at": fixed_now.isoformat()}),
                patch.object(
                    trade_automation_service,
                    "_build_live_current_route_collection_metrics",
                    return_value={
                        "current_route_fill_count": 0,
                        "current_route_closed_trade_count": 0,
                        "current_route_mismatched_count": 0,
                        "current_route_sample_status": "insufficient",
                        "current_route_reconciliation_status": "waiting",
                        "current_route_orphan_order_event_count": 0,
                        "mark_to_market_coverage_status": "missing",
                        "ledger_snapshot_consistency": "unavailable",
                        "metrics_source": "event_ledger",
                        "route_window_start": None,
                        "route_window_end": None,
                        "route_window_snapshot_count": 0,
                        "current_route_latest_event_at": None,
                        "current_route_validation_integrity": {"status": "partial"},
                    },
                ),
            ):
                snapshot = trade_automation_service._run_trade_automation_cycle(db, tenant=tenant, state=state, forced=False, actor=None)

        self.assertEqual(snapshot["runtime"]["last_collection_blocker"], "no_candidates")
        self.assertEqual(snapshot["collection_phase"]["collection_phase_label"], "No eligible candidates")
        self.assertEqual(snapshot["runtime"]["last_collection_audit"]["scanned_candidate_count"], 0)

    def test_trade_automation_cycle_marks_broker_submit_failure_collection_blocker(self) -> None:
        from backend.core.database import Base
        from backend.services import tenant_service, trade_automation_service

        engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
        Session = sessionmaker(bind=engine, expire_on_commit=False, future=True)
        Base.metadata.create_all(bind=engine)

        fake_settings = SimpleNamespace(
            demo_tenant_slug="alpha-desk",
            demo_tenant_name="Alpha Desk",
            demo_tenant_plan="pro",
        )
        fixed_now = datetime(2026, 4, 20, 15, 0, tzinfo=timezone.utc)
        watchlist = {
            "rows": [
                {
                    "ticker": "AAPL",
                    "trade_decision": "VALID TRADE",
                    "verdict": "BULLISH",
                    "ranking_tier": "promote",
                    "alpha_score": 91.0,
                    "execution_score": 82.0,
                    "portfolio_score": 88.0,
                    "portfolio_rank": 1,
                    "auto_entry_eligible": True,
                    "event_risk": False,
                    "proxy_correlation_bucket": "mega_cap_tech",
                    "projected_position_cost": 1200.0,
                    "live_price": 105.0,
                    "close": 105.0,
                    "spread_bps": 8.0,
                    "average_dollar_volume": 5_000_000_000.0,
                    "average_1m_dollar_volume": 2_000_000.0,
                    "edge_to_cost_ratio": 3.4,
                }
            ],
            "path_evaluations": [],
        }

        with Session() as db, patch.object(tenant_service, "settings", fake_settings):
            identity = tenant_service.ensure_demo_tenant_for_user(
                db,
                auth_subject="demo-trader",
                email="demo@example.test",
                name="Demo Trader",
                provider="local-demo",
                requested_tenant_slug="alpha-desk",
            )
            tenant = identity["active_tenant"]
            state = trade_automation_service._read_trade_automation_state(tenant)
            state["settings"].update(
                {
                    "enabled": True,
                    "armed": True,
                    "execution_intent": "broker_live",
                    "tickers": ["AAPL"],
                    "account_size": 10000.0,
                    "risk_percent": 0.5,
                    "max_open_positions": 2,
                    "allow_pyramiding": True,
                    "require_liquidity_fields": True,
                    "min_edge_to_cost_ratio": 2.5,
                    "cycle_entry_rank_limit": 2,
                }
            )

            with (
                patch.object(trade_automation_service, "_utc_now", return_value=fixed_now),
                patch.object(
                    trade_automation_service,
                    "get_trade_summary",
                    return_value={"rollout_readiness": {"ranked_entry_rollout": {"accepted": False, "status": "partial"}}},
                ),
                patch.object(trade_automation_service, "sync_pending_orders_from_broker", return_value=None),
                patch.object(trade_automation_service, "_manage_automation_positions", return_value={"acted_count": 0, "failed_count": 0, "items": [], "failed_items": []}),
                patch.object(trade_automation_service, "_persist_watchlist_validation_snapshot", return_value=None),
                patch.object(trade_automation_service, "build_watchlist", return_value=watchlist),
                patch.object(trade_automation_service.sdm, "read_open_trades", return_value=pd.DataFrame()),
                patch.object(trade_automation_service.sdm, "read_pending_orders", return_value=pd.DataFrame()),
                patch.object(trade_automation_service.sdm, "read_closed_trades", return_value=pd.DataFrame()),
                patch.object(trade_automation_service.sdm, "monitor_open_trades", return_value=pd.DataFrame()),
                patch.object(trade_automation_service, "record_trade_automation_equity_snapshot", return_value={"cycle_id": "cycle-1", "cycle_at": fixed_now.isoformat()}),
                patch.object(
                    trade_automation_service,
                    "_build_live_current_route_collection_metrics",
                    return_value={
                        "current_route_fill_count": 0,
                        "current_route_closed_trade_count": 0,
                        "current_route_mismatched_count": 0,
                        "current_route_sample_status": "insufficient",
                        "current_route_reconciliation_status": "waiting",
                        "current_route_orphan_order_event_count": 0,
                        "mark_to_market_coverage_status": "missing",
                        "ledger_snapshot_consistency": "unavailable",
                        "metrics_source": "event_ledger",
                        "route_window_start": None,
                        "route_window_end": None,
                        "route_window_snapshot_count": 0,
                        "current_route_latest_event_at": None,
                        "current_route_validation_integrity": {"status": "partial"},
                    },
                ),
                patch.object(trade_automation_service, "open_trade_from_request", side_effect=RuntimeError("submit failed")),
            ):
                snapshot = trade_automation_service._run_trade_automation_cycle(db, tenant=tenant, state=state, forced=False, actor=None)

        self.assertEqual(snapshot["runtime"]["last_collection_blocker"], "broker_submit_failed")
        self.assertEqual(snapshot["runtime"]["last_collection_audit"]["primary_blocker"], "broker_submit_failed")
        self.assertEqual(snapshot["runtime"]["last_collection_audit"]["submitted_order_count"], 1)
        self.assertEqual(snapshot["runtime"]["last_collection_audit"]["broker_acknowledgement_count"], 0)

    def test_trade_automation_cycle_marks_ledger_persistence_failure_collection_blocker(self) -> None:
        from backend.core.database import Base
        from backend.services import tenant_service, trade_automation_service
        from backend.services.exceptions import ValidationError

        engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
        Session = sessionmaker(bind=engine, expire_on_commit=False, future=True)
        Base.metadata.create_all(bind=engine)

        fake_settings = SimpleNamespace(
            demo_tenant_slug="alpha-desk",
            demo_tenant_name="Alpha Desk",
            demo_tenant_plan="pro",
        )
        fixed_now = datetime(2026, 4, 20, 15, 0, tzinfo=timezone.utc)
        watchlist = {
            "rows": [
                {
                    "ticker": "AAPL",
                    "trade_decision": "VALID TRADE",
                    "verdict": "BULLISH",
                    "ranking_tier": "promote",
                    "alpha_score": 91.0,
                    "execution_score": 82.0,
                    "portfolio_score": 88.0,
                    "portfolio_rank": 1,
                    "auto_entry_eligible": True,
                    "event_risk": False,
                    "proxy_correlation_bucket": "mega_cap_tech",
                    "projected_position_cost": 1200.0,
                    "live_price": 105.0,
                    "close": 105.0,
                    "spread_bps": 8.0,
                    "average_dollar_volume": 5_000_000_000.0,
                    "average_1m_dollar_volume": 2_000_000.0,
                    "edge_to_cost_ratio": 3.4,
                }
            ],
            "path_evaluations": [],
        }

        with Session() as db, patch.object(tenant_service, "settings", fake_settings):
            identity = tenant_service.ensure_demo_tenant_for_user(
                db,
                auth_subject="demo-trader",
                email="demo@example.test",
                name="Demo Trader",
                provider="local-demo",
                requested_tenant_slug="alpha-desk",
            )
            tenant = identity["active_tenant"]
            state = trade_automation_service._read_trade_automation_state(tenant)
            state["settings"].update(
                {
                    "enabled": True,
                    "armed": True,
                    "execution_intent": "broker_live",
                    "tickers": ["AAPL"],
                    "account_size": 10000.0,
                    "risk_percent": 0.5,
                    "max_open_positions": 2,
                    "allow_pyramiding": True,
                    "require_liquidity_fields": True,
                    "min_edge_to_cost_ratio": 2.5,
                    "cycle_entry_rank_limit": 2,
                }
            )

            with (
                patch.object(trade_automation_service, "_utc_now", return_value=fixed_now),
                patch.object(
                    trade_automation_service,
                    "get_trade_summary",
                    return_value={"rollout_readiness": {"ranked_entry_rollout": {"accepted": False, "status": "partial"}}},
                ),
                patch.object(trade_automation_service, "sync_pending_orders_from_broker", return_value=None),
                patch.object(trade_automation_service, "_manage_automation_positions", return_value={"acted_count": 0, "failed_count": 0, "items": [], "failed_items": []}),
                patch.object(trade_automation_service, "_persist_watchlist_validation_snapshot", return_value=None),
                patch.object(trade_automation_service, "build_watchlist", return_value=watchlist),
                patch.object(trade_automation_service.sdm, "read_open_trades", return_value=pd.DataFrame()),
                patch.object(trade_automation_service.sdm, "read_pending_orders", return_value=pd.DataFrame()),
                patch.object(trade_automation_service.sdm, "read_closed_trades", return_value=pd.DataFrame()),
                patch.object(trade_automation_service.sdm, "monitor_open_trades", return_value=pd.DataFrame()),
                patch.object(trade_automation_service, "record_trade_automation_equity_snapshot", return_value={"cycle_id": "cycle-1", "cycle_at": fixed_now.isoformat()}),
                patch.object(
                    trade_automation_service,
                    "_build_live_current_route_collection_metrics",
                    return_value={
                        "current_route_fill_count": 0,
                        "current_route_closed_trade_count": 0,
                        "current_route_mismatched_count": 0,
                        "current_route_sample_status": "insufficient",
                        "current_route_reconciliation_status": "waiting",
                        "current_route_orphan_order_event_count": 0,
                        "mark_to_market_coverage_status": "missing",
                        "ledger_snapshot_consistency": "unavailable",
                        "metrics_source": "event_ledger",
                        "route_window_start": None,
                        "route_window_end": None,
                        "route_window_snapshot_count": 0,
                        "current_route_latest_event_at": None,
                        "current_route_validation_integrity": {"status": "partial"},
                    },
                ),
                patch.object(
                    trade_automation_service,
                    "open_trade_from_request",
                    side_effect=ValidationError(
                        "Broker-paper order was accepted, but the local trade ledger row was not persisted.",
                        error_code="ledger_persistence_failed",
                        details={"collection_blocker": "ledger_persistence_failed"},
                    ),
                ),
            ):
                snapshot = trade_automation_service._run_trade_automation_cycle(db, tenant=tenant, state=state, forced=False, actor=None)

        self.assertEqual(snapshot["runtime"]["last_collection_blocker"], "ledger_persistence_failed")
        self.assertEqual(snapshot["runtime"]["last_collection_audit"]["primary_blocker"], "ledger_persistence_failed")
        self.assertEqual(snapshot["collection_phase"]["collection_phase_label"], "Local ledger persistence issue")
        self.assertTrue(snapshot["collection_phase"]["collection_phase_active"])

    def test_trade_automation_finalize_reruns_validation_once_for_same_qualified_sample(self) -> None:
        from backend.services import trade_automation_service

        tenant = SimpleNamespace(id="tenant-1", slug="alpha-desk", metadata_json={})
        state = {
            "settings": {
                "enabled": True,
                "armed": True,
                "execution_intent": "broker_live",
                "regular_hours_only": False,
                "cycle_interval_seconds": 60,
                "flatten_before_close_minutes": 15,
            },
            "runtime": {
                "auto_validation_rerun_enabled": True,
            },
            "history": [],
        }
        qualifying_metrics = {
            "current_route_fill_count": 10,
            "current_route_closed_trade_count": 5,
            "current_route_mismatched_count": 0,
            "current_route_sample_status": "sufficient",
            "current_route_reconciliation_status": "clean",
            "current_route_orphan_order_event_count": 0,
            "mark_to_market_coverage_status": "complete",
            "ledger_snapshot_consistency": "consistent",
            "metrics_source": "mark_to_market",
            "route_window_start": "2026-04-19T14:35:00+00:00",
            "route_window_end": "2026-04-19T15:05:00+00:00",
            "route_window_snapshot_count": 12,
            "current_route_latest_event_at": "2026-04-19T15:05:00+00:00",
            "current_route_validation_integrity": {"status": "pass", "metrics_source": "mark_to_market"},
        }

        with (
            patch.object(trade_automation_service, "_write_trade_automation_state", return_value=None),
            patch.object(trade_automation_service, "record_trade_automation_equity_snapshot", return_value={"cycle_id": "cycle-1"}),
            patch.object(trade_automation_service, "_build_live_current_route_collection_metrics", return_value=qualifying_metrics),
            patch.object(
                trade_automation_service,
                "get_trade_summary",
                return_value={
                    "rollout_readiness": {
                        "ranked_entry_rollout": {
                            "accepted": False,
                            "status": "partial",
                        }
                    }
                },
            ),
            patch.object(trade_automation_service, "_build_snapshot_payload", side_effect=lambda tenant, state, **kwargs: {"runtime": dict(state["runtime"]), "rollout_readiness": dict(kwargs.get("rollout_readiness") or {})}),
            patch.object(trade_automation_service, "_run_validation_export_for_collection_phase", return_value=None) as rerun_mock,
            patch.object(
                trade_automation_service,
                "_load_latest_validation_summary_generated_at",
                side_effect=[
                    None,
                    datetime(2026, 4, 19, 15, 6, tzinfo=timezone.utc),
                ],
            ),
        ):
            first_snapshot = trade_automation_service._finalize_trade_automation_cycle(
                MagicMock(),
                tenant=tenant,
                state=state,
                rollout_readiness={"ranked_entry_rollout": {"accepted": False, "status": "partial"}},
                now=datetime(2026, 4, 19, 15, 6, tzinfo=timezone.utc),
                cycle_id="cycle-1",
            )
            second_snapshot = trade_automation_service._finalize_trade_automation_cycle(
                MagicMock(),
                tenant=tenant,
                state=state,
                rollout_readiness={"ranked_entry_rollout": {"accepted": False, "status": "partial"}},
                now=datetime(2026, 4, 19, 15, 7, tzinfo=timezone.utc),
                cycle_id="cycle-2",
            )

        self.assertEqual(rerun_mock.call_count, 1)
        self.assertEqual(first_snapshot["runtime"]["last_validation_rerun_cycle_id"], "cycle-1")
        self.assertEqual(second_snapshot["runtime"]["last_validation_rerun_cycle_id"], "cycle-1")

    def test_trade_automation_finalize_skips_validation_rerun_below_sample_threshold(self) -> None:
        from backend.services import trade_automation_service

        tenant = SimpleNamespace(id="tenant-1", slug="alpha-desk", metadata_json={})
        state = {
            "settings": {
                "enabled": True,
                "armed": True,
                "execution_intent": "broker_live",
                "regular_hours_only": False,
                "cycle_interval_seconds": 60,
                "flatten_before_close_minutes": 15,
            },
            "runtime": {
                "auto_validation_rerun_enabled": True,
            },
            "history": [],
        }

        with (
            patch.object(trade_automation_service, "_write_trade_automation_state", return_value=None),
            patch.object(trade_automation_service, "record_trade_automation_equity_snapshot", return_value={"cycle_id": "cycle-1"}),
            patch.object(
                trade_automation_service,
                "_build_live_current_route_collection_metrics",
                return_value={
                    "current_route_fill_count": 6,
                    "current_route_closed_trade_count": 2,
                    "current_route_mismatched_count": 0,
                    "current_route_sample_status": "insufficient",
                    "current_route_reconciliation_status": "waiting",
                    "current_route_orphan_order_event_count": 0,
                    "mark_to_market_coverage_status": "missing",
                    "ledger_snapshot_consistency": "unavailable",
                    "metrics_source": "event_ledger",
                    "route_window_start": None,
                    "route_window_end": None,
                    "route_window_snapshot_count": 0,
                    "current_route_latest_event_at": None,
                    "current_route_validation_integrity": {"status": "partial"},
                },
            ),
            patch.object(trade_automation_service, "_build_snapshot_payload", side_effect=lambda tenant, state, **kwargs: {"runtime": dict(state["runtime"]), "collection_phase": dict(kwargs.get("rollout_readiness") or {})}),
            patch.object(trade_automation_service, "_run_validation_export_for_collection_phase", return_value=None) as rerun_mock,
        ):
            snapshot = trade_automation_service._finalize_trade_automation_cycle(
                MagicMock(),
                tenant=tenant,
                state=state,
                rollout_readiness={"ranked_entry_rollout": {"accepted": False, "status": "partial"}},
                now=datetime(2026, 4, 19, 15, 6, tzinfo=timezone.utc),
                cycle_id="cycle-1",
            )

        self.assertEqual(rerun_mock.call_count, 0)
        self.assertIsNone(snapshot["runtime"]["last_validation_rerun_cycle_id"])

    def test_trade_automation_cycle_keeps_collection_phase_locked_to_paper_when_ranked_entry_gate_fails(self) -> None:
        from backend.core.database import Base
        from backend.services import tenant_service, trade_automation_service

        engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
        Session = sessionmaker(bind=engine, expire_on_commit=False, future=True)
        Base.metadata.create_all(bind=engine)

        fake_settings = SimpleNamespace(
            demo_tenant_slug="alpha-desk",
            demo_tenant_name="Alpha Desk",
            demo_tenant_plan="pro",
        )
        with Session() as db, patch.object(tenant_service, "settings", fake_settings):
            identity = tenant_service.ensure_demo_tenant_for_user(
                db,
                auth_subject="demo-trader",
                email="demo@example.test",
                name="Demo Trader",
                provider="local-demo",
                requested_tenant_slug="alpha-desk",
            )
            tenant = identity["active_tenant"]
            state = trade_automation_service._read_trade_automation_state(tenant)
            state["settings"].update(
                {
                    "enabled": True,
                    "armed": True,
                    "execution_intent": "broker_live",
                    "tickers": ["AAPL"],
                    "account_size": 10000.0,
                    "risk_percent": 0.5,
                    "max_open_positions": 2,
                    "max_notional_per_trade": 1500.0,
                    "max_total_open_notional": 15000.0,
                    "allow_pyramiding": True,
                    "require_liquidity_fields": True,
                    "min_edge_to_cost_ratio": 2.5,
                    "cycle_entry_rank_limit": 2,
                }
            )
            watchlist = {
                "rows": [
                    {
                        "ticker": "AAPL",
                        "trade_decision": "VALID TRADE",
                        "verdict": "BULLISH",
                        "ranking_tier": "promote",
                        "alpha_score": 91.0,
                        "execution_score": 82.0,
                        "portfolio_score": 88.0,
                        "portfolio_rank": 1,
                        "event_risk": False,
                        "proxy_correlation_bucket": "mega_cap_tech",
                        "projected_position_cost": 1200.0,
                        "live_price": 105.0,
                        "close": 105.0,
                        "spread_bps": 8.0,
                        "average_dollar_volume": 5_000_000_000.0,
                        "average_1m_dollar_volume": 2_000_000.0,
                        "edge_to_cost_ratio": 3.4,
                    }
                ]
            }

            with (
                patch.object(trade_automation_service, "_utc_now", return_value=datetime(2026, 4, 20, 15, 0, tzinfo=timezone.utc)),
                patch.object(
                    trade_automation_service,
                    "get_trade_summary",
                    return_value={
                        "rollout_readiness": {
                            "status": "locked",
                            "label": "Validation only",
                            "allows_live_rollout": False,
                            "basis": "Candidate drawdown 12.00% exceeds the allowed 11.50% ceiling.",
                            "ranked_entry_rollout": {
                                "status": "rejected",
                                "accepted": False,
                                "candidate_key": "M",
                            },
                        }
                    },
                ),
                patch.object(trade_automation_service, "sync_pending_orders_from_broker", return_value=None),
                patch.object(trade_automation_service, "_manage_automation_positions", return_value={"acted_count": 0, "failed_count": 0, "items": [], "failed_items": []}),
                patch.object(trade_automation_service, "_persist_watchlist_validation_snapshot", return_value=None),
                patch.object(trade_automation_service, "build_watchlist", return_value=watchlist),
                patch.object(trade_automation_service.sdm, "read_open_trades", return_value=pd.DataFrame()),
                patch.object(trade_automation_service.sdm, "read_pending_orders", return_value=pd.DataFrame()),
                patch.object(trade_automation_service.sdm, "read_closed_trades", return_value=pd.DataFrame()),
                patch.object(trade_automation_service.sdm, "monitor_open_trades", return_value=pd.DataFrame()),
                patch.object(trade_automation_service.sdm, "update_open_trade", return_value=None),
                patch.object(trade_automation_service, "record_trade_automation_equity_snapshot", return_value={"cycle_id": "cycle-1", "cycle_at": datetime(2026, 4, 20, 15, 0, tzinfo=timezone.utc).isoformat()}),
                patch.object(
                    trade_automation_service,
                    "_build_live_current_route_collection_metrics",
                    return_value={
                        "current_route_fill_count": 10,
                        "current_route_closed_trade_count": 5,
                        "current_route_mismatched_count": 0,
                        "current_route_sample_status": "sufficient",
                        "mark_to_market_coverage_status": "complete",
                        "ledger_snapshot_consistency": "consistent",
                        "metrics_source": "mark_to_market",
                        "route_window_start": "2026-04-19T14:35:00+00:00",
                        "route_window_end": "2026-04-19T15:05:00+00:00",
                        "route_window_snapshot_count": 12,
                        "current_route_latest_event_at": "2026-04-19T15:05:00+00:00",
                        "current_route_validation_integrity": {"status": "pass", "metrics_source": "mark_to_market"},
                    },
                ),
                patch.object(trade_automation_service, "open_trade_from_request", return_value={"position_opened": True, "record": {"trade_id": "trade-1", "order_id": "order-1"}, "pending_order": {}, "execution": {"intent": "broker_paper"}}) as open_mock,
            ):
                snapshot = trade_automation_service._run_trade_automation_cycle(db, tenant=tenant, state=state, forced=False, actor=None)

        request = open_mock.call_args.args[0]
        self.assertEqual(request.execution_intent, "broker_paper")
        self.assertFalse(request.regular_hours_only)
        self.assertEqual(request.order_type, "limit")
        self.assertEqual(request.time_in_force, "day")
        self.assertFalse(request.extended_hours)
        self.assertEqual(snapshot["settings"]["execution_intent"], "broker_paper")
        self.assertTrue(snapshot["collection_phase"]["collection_phase_active"])
        self.assertEqual(snapshot["collection_phase"]["collection_phase_label"], "Validation still blocked")

    def test_trade_automation_cycle_auto_kills_after_error_streak(self) -> None:
        from backend.core.database import Base
        from backend.services import tenant_service, trade_automation_service

        engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
        Session = sessionmaker(bind=engine, expire_on_commit=False, future=True)
        Base.metadata.create_all(bind=engine)

        fake_settings = SimpleNamespace(
            demo_tenant_slug="alpha-desk",
            demo_tenant_name="Alpha Desk",
            demo_tenant_plan="pro",
        )
        with Session() as db, patch.object(tenant_service, "settings", fake_settings):
            identity = tenant_service.ensure_demo_tenant_for_user(
                db,
                auth_subject="demo-trader",
                email="demo@example.test",
                name="Demo Trader",
                provider="local-demo",
                requested_tenant_slug="alpha-desk",
            )
            tenant = identity["active_tenant"]
            state = trade_automation_service._read_trade_automation_state(tenant)
            state["settings"].update(
                {
                    "enabled": True,
                    "armed": True,
                    "max_error_streak": 2,
                }
            )
            state["runtime"]["error_streak"] = 1

            with (
                patch.object(trade_automation_service, "_utc_now", return_value=datetime(2026, 4, 20, 15, 0, tzinfo=timezone.utc)),
                patch.object(trade_automation_service, "get_trade_summary", return_value={"rollout_readiness": {}}),
                patch.object(trade_automation_service, "sync_pending_orders_from_broker", side_effect=RuntimeError("sync failed")),
                patch.object(trade_automation_service.sdm, "read_open_trades", return_value=pd.DataFrame()),
                patch.object(trade_automation_service.sdm, "read_pending_orders", return_value=pd.DataFrame()),
                patch.object(trade_automation_service.sdm, "read_closed_trades", return_value=pd.DataFrame()),
            ):
                snapshot = trade_automation_service._run_trade_automation_cycle(db, tenant=tenant, state=state, forced=False, actor=None)

        self.assertTrue(snapshot["settings"]["kill_switch"])
        self.assertEqual(snapshot["runtime"]["error_streak"], 2)
        self.assertEqual(snapshot["guardrails"]["status"]["reason"], "error_streak_lock")
        self.assertEqual(snapshot["runtime"]["last_guardrail"]["reason"], "error_streak_lock")

    def test_open_trade_from_request_blocks_bearish_equity_entry_when_long_only(self) -> None:
        from backend.schemas import OpenTradeRequest
        from backend.services import trade_service

        request = OpenTradeRequest(
            ticker="SPY",
            interval="5m",
            horizon=5,
            live_price=100.0,
            account_size=10000.0,
            risk_percent=0.25,
            instrument_type="equity",
            execution_intent="desk",
            order_type="market",
            time_in_force="day",
            long_only=True,
        )
        bearish_report = {"verdict": "BEARISH"}

        with (
            patch.object(trade_service, "_record_order_event", return_value=None),
            patch.object(trade_service.sdm, "read_open_trades", return_value=pd.DataFrame()),
            patch.object(trade_service.sdm, "read_pending_orders", return_value=pd.DataFrame()),
            patch.object(trade_service.sdm, "read_closed_trades", return_value=pd.DataFrame()),
            patch.object(trade_service, "_build_rollout_readiness_snapshot", return_value={}),
            patch.object(trade_service, "get_order_lifecycle_health_snapshot", return_value={}),
            patch.object(trade_service, "_resolve_execution_adapter_for_open_request", return_value=(MagicMock(), "desk", "desk")),
            patch.object(trade_service, "analyze_market", return_value={"report": bearish_report, "live_price": 100.0}),
        ):
            with self.assertRaises(ValidationServiceError):
                trade_service.open_trade_from_request(request)

    def test_get_next_earnings_event_skips_earnings_lookup_for_etf_symbols(self) -> None:
        from backend import stock_direction_model

        provider = MagicMock()
        with patch.dict(stock_direction_model._EARNINGS_CACHE, {}, clear=True):
            with patch.object(stock_direction_model, "get_market_data_provider", return_value=provider):
                event_name, event_date = stock_direction_model.get_next_earnings_event("SPY")

        self.assertEqual(event_name, "")
        self.assertEqual(event_date, "")
        provider.get_calendar_events.assert_not_called()
        provider.get_earnings_events.assert_not_called()


if __name__ == "__main__":
    unittest.main()
