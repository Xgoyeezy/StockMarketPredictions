from __future__ import annotations

from backend.core.config import settings
from backend.services.exceptions import ValidationServiceError
from backend.services.execution.alpaca_client import AlpacaTradingClient, build_alpaca_live_client
from backend.services.execution.alpaca_paper_adapter import AlpacaPaperExecutionAdapter


class AlpacaLiveExecutionAdapter(AlpacaPaperExecutionAdapter):
    def __init__(self, client: AlpacaTradingClient | None = None) -> None:
        super().__init__(client=client or build_alpaca_live_client())

    @property
    def adapter_name(self) -> str:
        return "alpaca_live"

    def _ensure_credentials(self) -> None:
        if not settings.alpaca_live_trading_enabled:
            raise ValidationServiceError(
                "Alpaca live trading is disabled. Turn on ALPACA_LIVE_TRADING_ENABLED before routing broker-live orders."
            )
        if not (settings.alpaca_live_api_key_id or settings.alpaca_api_key_id) or not (
            settings.alpaca_live_api_secret_key or settings.alpaca_api_secret_key
        ):
            raise ValidationServiceError(
                "Alpaca live execution requires live Alpaca credentials or the shared Alpaca key pair."
            )
