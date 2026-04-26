from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Sequence

import pandas as pd

from backend.services.market_data.types import (
    EventRevisionSnapshot,
    MarketEvent,
    MarketNewsItem,
    MarketStateSnapshot,
    OptionChainSnapshot,
    OptionsFlowSnapshot,
    RelativeStrengthSnapshot,
)


class MarketDataProvider(ABC):
    @property
    @abstractmethod
    def provider_name(self) -> str:
        raise NotImplementedError

    @abstractmethod
    def download_bars(
        self,
        tickers: str | Sequence[str],
        *,
        period: str,
        interval: str,
        prepost: bool,
        group_by: str | None = None,
    ) -> pd.DataFrame:
        raise NotImplementedError

    @abstractmethod
    def get_contract_history(self, contract_symbol: str, *, period: str, interval: str) -> pd.DataFrame:
        raise NotImplementedError

    @abstractmethod
    def get_latest_prices(self, symbols: Sequence[str], *, prepost: bool) -> dict[str, float]:
        raise NotImplementedError

    @abstractmethod
    def get_calendar_events(self, ticker: str) -> list[MarketEvent]:
        raise NotImplementedError

    @abstractmethod
    def get_earnings_events(self, ticker: str, *, limit: int) -> list[MarketEvent]:
        raise NotImplementedError

    @abstractmethod
    def get_news_items(self, ticker: str) -> list[MarketNewsItem]:
        raise NotImplementedError

    @abstractmethod
    def get_option_expirations(self, ticker: str) -> list[str]:
        raise NotImplementedError

    @abstractmethod
    def get_option_chain(self, ticker: str, expiration: str) -> OptionChainSnapshot:
        raise NotImplementedError

    @abstractmethod
    def get_market_state_snapshot(self, ticker: str, *, interval: str) -> MarketStateSnapshot:
        raise NotImplementedError

    @abstractmethod
    def get_relative_strength_snapshot(
        self,
        ticker: str,
        *,
        interval: str,
        benchmark_symbol: str,
        sector_symbol: str | None,
        peer_symbols: Sequence[str],
    ) -> RelativeStrengthSnapshot:
        raise NotImplementedError

    @abstractmethod
    def get_options_flow_snapshot(
        self,
        ticker: str,
        *,
        underlying_price: float | None = None,
        expiration: str | None = None,
    ) -> OptionsFlowSnapshot:
        raise NotImplementedError

    @abstractmethod
    def get_event_revision_snapshot(self, ticker: str) -> EventRevisionSnapshot:
        raise NotImplementedError
