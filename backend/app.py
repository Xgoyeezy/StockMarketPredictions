from __future__ import annotations

import uvicorn

from backend.core.config import settings


if __name__ == "__main__":
    uvicorn.run(
        "backend.api:app",
        host=settings.host,
        port=settings.port,
        reload=settings.reload,
        log_level="info",
    )
