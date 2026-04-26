from __future__ import annotations

from typing import Any

from backend.backend_service import run_scan
from backend.schemas import ScanRequest


def scan_market(
    tickers: list[str],
    interval: str = "5m",
    horizon: int = 5,
    top_n: int = 10,
    include_errors: bool = True,
) -> dict[str, Any]:
    request = ScanRequest(
        tickers=tickers,
        interval=interval,
        horizon=horizon,
        top_n=top_n,
        include_errors=include_errors,
    )
    return run_scan(request)
