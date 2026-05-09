from __future__ import annotations

from functools import lru_cache

from backend.core.config import settings
from backend.services.execution.alpaca_live_adapter import AlpacaLiveExecutionAdapter
from backend.services.execution.alpaca_paper_adapter import AlpacaPaperExecutionAdapter
from backend.services.execution.base import ExecutionAdapter
from backend.services.execution.desk_adapter import DeskExecutionAdapter
from backend.services.execution.internal_paper_adapter import InternalPaperExecutionAdapter
from backend.services.execution.legitimate_brokerage_paper_adapter import LegitimateBrokeragePaperExecutionAdapter
from backend.services.execution.tradier_paper_adapter import TradierPaperExecutionAdapter


@lru_cache(maxsize=4)
def get_execution_adapter_for(adapter_name: str) -> ExecutionAdapter:
    adapter_name = str(adapter_name or "desk").strip().lower()
    if adapter_name == "desk":
        return DeskExecutionAdapter()
    if adapter_name in {"internal", "internal_paper", "internal_simulator"}:
        return InternalPaperExecutionAdapter()
    if adapter_name in {"legitimate_brokerage", "legitimate_brokerage_paper", "legitimate"}:
        return LegitimateBrokeragePaperExecutionAdapter()
    if adapter_name == "alpaca_paper":
        return AlpacaPaperExecutionAdapter()
    if adapter_name == "alpaca_live":
        return AlpacaLiveExecutionAdapter()
    if adapter_name == "tradier_paper":
        return TradierPaperExecutionAdapter()
    raise ValueError(f"Unsupported execution adapter: {adapter_name}")


@lru_cache(maxsize=1)
def get_execution_adapter() -> ExecutionAdapter:
    adapter_name = str(getattr(settings, "execution_adapter", "desk") or "desk").strip().lower()
    return get_execution_adapter_for(adapter_name)
