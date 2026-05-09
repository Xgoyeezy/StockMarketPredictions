from __future__ import annotations

import asyncio
import sys

import uvicorn

from backend.core.config import settings


if __name__ == "__main__":
    if sys.platform.startswith("win"):
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

    uvicorn.run(
        "backend.api:app",
        host=settings.host,
        port=settings.port,
        reload=settings.reload,
        log_level="info",
    )
