from __future__ import annotations

import json
from abc import ABC, abstractmethod
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from typing import Any
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import pandas as pd

from backend import stock_direction_model as sdm
from backend.core.config import settings


class IntradayBarProvider(ABC):
    @property
    @abstractmethod
    def provider_name(self) -> str:
        raise NotImplementedError

    @abstractmethod
    def load_intraday_bars(self, symbol: str, *, days: int) -> pd.DataFrame:
        raise NotImplementedError


def _alpaca_rest_base_url() -> str:
    return "https://data.sandbox.alpaca.markets" if settings.alpaca_use_sandbox else "https://data.alpaca.markets"


class AlpacaIntradayBarProvider(IntradayBarProvider):
    @property
    def provider_name(self) -> str:
        return "alpaca"

    def load_intraday_bars(self, symbol: str, *, days: int) -> pd.DataFrame:
        if not (settings.alpaca_api_key_id and settings.alpaca_api_secret_key):
            return pd.DataFrame()

        normalized_symbol = str(symbol or "").strip().upper()
        if not normalized_symbol:
            return pd.DataFrame()

        end_at = datetime.now(timezone.utc)
        start_at = end_at - timedelta(days=max(days, 21))
        request_headers = {
            "APCA-API-KEY-ID": settings.alpaca_api_key_id,
            "APCA-API-SECRET-KEY": settings.alpaca_api_secret_key,
            "Accept": "application/json",
            "User-Agent": "StockMarketPredictions/2.6",
        }

        bars: list[dict[str, Any]] = []
        next_page_token: str | None = None

        while True:
            params = {
                "timeframe": "1Min",
                "start": start_at.isoformat().replace("+00:00", "Z"),
                "end": end_at.isoformat().replace("+00:00", "Z"),
                "adjustment": "all",
                "sort": "asc",
                "limit": 10000,
                "feed": settings.alpaca_stock_feed,
            }
            if next_page_token:
                params["page_token"] = next_page_token

            url = f"{_alpaca_rest_base_url()}/v2/stocks/{normalized_symbol}/bars?{urlencode(params)}"
            request = Request(url, headers=request_headers, method="GET")

            try:
                with urlopen(request, timeout=12) as response:
                    payload = json.loads(response.read().decode("utf-8"))
            except Exception:
                return pd.DataFrame()

            chunk = payload.get("bars", [])
            if isinstance(chunk, list):
                bars.extend(chunk)

            next_page_token = payload.get("next_page_token")
            if not next_page_token:
                break

        if not bars:
            return pd.DataFrame()

        return pd.DataFrame(
            {
                "datetime": [item.get("t") for item in bars],
                "open": [item.get("o") for item in bars],
                "high": [item.get("h") for item in bars],
                "low": [item.get("l") for item in bars],
                "close": [item.get("c") for item in bars],
                "volume": [item.get("v") for item in bars],
                "vwap": [item.get("vw") for item in bars],
                "trade_count": [item.get("n") for item in bars],
            }
        ).set_index("datetime")


class FallbackIntradayBarProvider(IntradayBarProvider):
    @property
    def provider_name(self) -> str:
        return "desk-fallback"

    def load_intraday_bars(self, symbol: str, *, days: int) -> pd.DataFrame:
        try:
            return sdm.download_ohlcv(symbol, "60d", "5m")
        except Exception:
            return pd.DataFrame()


@lru_cache(maxsize=1)
def get_intraday_bar_provider() -> IntradayBarProvider:
    provider_name = str(getattr(settings, "market_data_provider", "alpaca") or "alpaca").strip().lower()
    if provider_name == "alpaca" and settings.alpaca_api_key_id and settings.alpaca_api_secret_key:
        return AlpacaIntradayBarProvider()
    return FallbackIntradayBarProvider()


@lru_cache(maxsize=1)
def get_intraday_fallback_provider() -> IntradayBarProvider:
    return FallbackIntradayBarProvider()
