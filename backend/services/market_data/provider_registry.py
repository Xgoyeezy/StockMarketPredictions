from __future__ import annotations

from functools import lru_cache

from backend.core.config import settings
from backend.services.market_data.base import MarketDataProvider
from backend.services.market_data.hybrid_adapter import HybridMarketDataProvider
from backend.services.market_data.yfinance_adapter import YFinanceMarketDataProvider


@lru_cache(maxsize=1)
def get_market_data_provider() -> MarketDataProvider:
    provider_name = str(getattr(settings, "market_data_adapter", "yfinance") or "yfinance").strip().lower()
    if provider_name == "hybrid":
        return HybridMarketDataProvider()
    if provider_name == "yfinance":
        return YFinanceMarketDataProvider()
    raise ValueError(f"Unsupported market data adapter: {provider_name}")
