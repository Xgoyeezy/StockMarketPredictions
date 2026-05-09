from __future__ import annotations

from typing import Any

from backend.core.config import settings


def get_execution_provider_diagnostics() -> dict[str, Any]:
    alpaca_paper_ready = bool(settings.alpaca_api_key_id and settings.alpaca_api_secret_key)
    alpaca_live_ready = bool(settings.alpaca_live_api_key_id and settings.alpaca_live_api_secret_key)
    tradier_ready = bool(settings.tradier_paper_token and settings.tradier_paper_account_id)

    return {
        "configured": {
            "execution_adapter": str(getattr(settings, "execution_adapter", "desk") or "desk"),
            "broker_mode": str(getattr(settings, "broker_mode", "internal_paper") or "internal_paper"),
            "paper_broker_provider": str(getattr(settings, "paper_broker_provider", "internal_paper") or "internal_paper"),
            "options_broker_provider": str(getattr(settings, "options_broker_provider", "alpaca") or "alpaca"),
        },
        "providers": {
            "alpaca_paper": {
                "credentials_present": alpaca_paper_ready,
                "detail": "APCA_API_KEY_ID + APCA_API_SECRET_KEY present." if alpaca_paper_ready else "Missing Alpaca paper credentials.",
            },
            "alpaca_live": {
                "credentials_present": alpaca_live_ready,
                "detail": "APCA_LIVE_API_KEY_ID + APCA_LIVE_API_SECRET_KEY present." if alpaca_live_ready else "Missing Alpaca live credentials.",
            },
            "tradier_paper": {
                "credentials_present": tradier_ready,
                "detail": "TRADIER_PAPER_TOKEN + TRADIER_PAPER_ACCOUNT_ID present." if tradier_ready else "Missing Tradier paper credentials.",
            },
        },
        "notes": [
            "Routing decisions are enforced by the control plane; broker adapters only translate/execute.",
            "Live routing remains gated by explicit live authorization/session policy.",
        ],
    }
