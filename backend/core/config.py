from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover - optional during first install
    load_dotenv = None


def _bool_env(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _int_env(name: str, default: int) -> int:
    value = os.getenv(name)
    if value is None:
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _float_env(name: str, default: float) -> float:
    value = os.getenv(name)
    if value is None:
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _str_tuple_env(name: str, default: tuple[str, ...]) -> tuple[str, ...]:
    value = os.getenv(name)
    if not value:
        return default
    items = tuple(part.strip() for part in value.split(",") if part.strip())
    return items or default


_BACKEND_DIR = Path(__file__).resolve().parents[1]
_PROJECT_DIR = Path(__file__).resolve().parents[2]
_FRONTEND_BASE_URL = os.getenv("FRONTEND_DEV_URL", "http://localhost:5173").rstrip("/")

if load_dotenv:
    for env_path in (_PROJECT_DIR / ".env", _BACKEND_DIR / ".env"):
        if env_path.exists():
            load_dotenv(env_path, override=False)

_ENVIRONMENT = os.getenv("APP_ENV", "development").strip().lower()


@dataclass(frozen=True)
class Settings:
    app_name: str = os.getenv("APP_NAME", "Stock Options Signal API")
    app_version: str = os.getenv("APP_VERSION", "2.6.0")
    app_phase: str = os.getenv("APP_PHASE", "release-candidate")
    enterprise_runtime_profile: str = os.getenv("ENTERPRISE_RUNTIME_PROFILE", "production").strip().lower() or "production"
    api_prefix: str = os.getenv("API_PREFIX", "/api")
    host: str = os.getenv("API_HOST", "0.0.0.0")
    port: int = _int_env("API_PORT", 8000)
    allow_origins: tuple[str, ...] = _str_tuple_env(
        "ALLOW_ORIGINS",
        ("http://localhost:5173", "http://127.0.0.1:5173", "http://localhost:3000"),
    )
    frontend_dev_url: str = os.getenv("FRONTEND_DEV_URL", "http://localhost:5173")
    billing_support_email: str = os.getenv("BILLING_SUPPORT_EMAIL", "billing@stocksignals.local")
    stripe_publishable_key: str = os.getenv("STRIPE_PUBLISHABLE_KEY", "").strip()
    stripe_secret_key: str = os.getenv("STRIPE_SECRET_KEY", "").strip()
    stripe_webhook_secret: str = os.getenv("STRIPE_WEBHOOK_SECRET", "").strip()
    billing_checkout_success_url: str = os.getenv("BILLING_CHECKOUT_SUCCESS_URL", f"{_FRONTEND_BASE_URL}/settings?billing=success")
    billing_checkout_cancel_url: str = os.getenv("BILLING_CHECKOUT_CANCEL_URL", f"{_FRONTEND_BASE_URL}/settings?billing=cancel")
    stripe_price_starter_monthly: str = os.getenv("STRIPE_PRICE_STARTER_MONTHLY", "").strip()
    stripe_price_starter_annual: str = os.getenv("STRIPE_PRICE_STARTER_ANNUAL", "").strip()
    stripe_price_pro_monthly: str = os.getenv("STRIPE_PRICE_PRO_MONTHLY", "").strip()
    stripe_price_pro_annual: str = os.getenv("STRIPE_PRICE_PRO_ANNUAL", "").strip()
    stripe_price_team_monthly: str = os.getenv("STRIPE_PRICE_TEAM_MONTHLY", "").strip()
    stripe_price_team_annual: str = os.getenv("STRIPE_PRICE_TEAM_ANNUAL", "").strip()
    stripe_price_enterprise_monthly: str = os.getenv("STRIPE_PRICE_ENTERPRISE_MONTHLY", "").strip()
    stripe_price_enterprise_annual: str = os.getenv("STRIPE_PRICE_ENTERPRISE_ANNUAL", "").strip()
    stripe_price_white_label_monthly: str = os.getenv("STRIPE_PRICE_WHITE_LABEL_MONTHLY", "").strip()
    stripe_price_white_label_annual: str = os.getenv("STRIPE_PRICE_WHITE_LABEL_ANNUAL", "").strip()
    environment: str = _ENVIRONMENT
    auth_enabled: bool = _bool_env("AUTH_ENABLED", False)
    allow_demo_auth: bool = _bool_env("ALLOW_DEMO_AUTH", _ENVIRONMENT in {"development", "local", "test"})
    demo_user_id: str = os.getenv("DEMO_USER_ID", "demo-trader")
    demo_user_email: str = os.getenv("DEMO_USER_EMAIL", "demo@stocksignals.local")
    demo_user_name: str = os.getenv("DEMO_USER_NAME", "Demo Trader")
    demo_tenant_slug: str = os.getenv("DEMO_TENANT_SLUG", "systematic-equities")
    demo_tenant_name: str = os.getenv("DEMO_TENANT_NAME", "Systematic Equities Desk")
    demo_tenant_plan: str = os.getenv("DEMO_TENANT_PLAN", "pro")
    auth_provider: str = os.getenv("AUTH_PROVIDER", "local-demo")
    public_api_base_url: str = os.getenv("PUBLIC_API_BASE_URL", f"http://localhost:{_int_env('API_PORT', 8000)}{os.getenv('API_PREFIX', '/api')}").rstrip("/")
    auth_session_cookie_name: str = os.getenv("AUTH_SESSION_COOKIE_NAME", "stocksignals_session").strip() or "stocksignals_session"
    auth_session_secret: str = os.getenv("AUTH_SESSION_SECRET", "stocksignals-local-session-secret").strip() or "stocksignals-local-session-secret"
    auth_session_max_age_seconds: int = _int_env("AUTH_SESSION_MAX_AGE_SECONDS", 60 * 60 * 24 * 14)
    auth_session_secure: bool = _bool_env("AUTH_SESSION_SECURE", _ENVIRONMENT == "production")
    auth_state_cookie_name: str = os.getenv("AUTH_STATE_COOKIE_NAME", "stocksignals_auth_state").strip() or "stocksignals_auth_state"
    auth_state_secret: str = os.getenv("AUTH_STATE_SECRET", "stocksignals-local-auth-state-secret").strip() or "stocksignals-local-auth-state-secret"
    auth_state_max_age_seconds: int = _int_env("AUTH_STATE_MAX_AGE_SECONDS", 60 * 10)
    local_auth_allow_signup: bool = _bool_env("LOCAL_AUTH_ALLOW_SIGNUP", True)
    local_auth_default_plan: str = os.getenv("LOCAL_AUTH_DEFAULT_PLAN", "starter").strip().lower() or "starter"
    local_auth_login_secret: str = os.getenv("LOCAL_AUTH_LOGIN_SECRET", "").strip()
    auth0_domain: str = os.getenv("AUTH0_DOMAIN", "").strip()
    auth0_client_id: str = os.getenv("AUTH0_CLIENT_ID", "").strip()
    auth0_client_secret: str = os.getenv("AUTH0_CLIENT_SECRET", "").strip()
    auth0_audience: str = os.getenv("AUTH0_AUDIENCE", "").strip()
    auth0_scope: str = os.getenv("AUTH0_SCOPE", "openid profile email").strip() or "openid profile email"
    auth0_organization: str = os.getenv("AUTH0_ORGANIZATION", "").strip()
    auth0_allow_signup: bool = _bool_env("AUTH0_ALLOW_SIGNUP", True)
    oidc_issuer: str = os.getenv("OIDC_ISSUER", "").strip()
    oidc_client_id: str = os.getenv("OIDC_CLIENT_ID", "").strip()
    oidc_client_secret: str = os.getenv("OIDC_CLIENT_SECRET", "").strip()
    oidc_scope: str = os.getenv("OIDC_SCOPE", "openid profile email").strip() or "openid profile email"
    oidc_audience: str = os.getenv("OIDC_AUDIENCE", "").strip()
    oidc_authorize_url: str = os.getenv("OIDC_AUTHORIZE_URL", "").strip()
    oidc_token_url: str = os.getenv("OIDC_TOKEN_URL", "").strip()
    oidc_userinfo_url: str = os.getenv("OIDC_USERINFO_URL", "").strip()
    oidc_logout_url: str = os.getenv("OIDC_LOGOUT_URL", "").strip()
    oidc_allow_signup: bool = _bool_env("OIDC_ALLOW_SIGNUP", True)
    auth_post_login_redirect_url: str = os.getenv("AUTH_POST_LOGIN_REDIRECT_URL", f"{_FRONTEND_BASE_URL}/").strip() or f"{_FRONTEND_BASE_URL}/"
    auth_post_logout_redirect_url: str = os.getenv("AUTH_POST_LOGOUT_REDIRECT_URL", f"{_FRONTEND_BASE_URL}/").strip() or f"{_FRONTEND_BASE_URL}/"
    api_token_salt: str = os.getenv("API_TOKEN_SALT", "stocksignals-local-token-salt").strip() or "stocksignals-local-token-salt"
    storage_dir: str = os.getenv("APP_STORAGE_DIR", str(_BACKEND_DIR / "storage"))
    database_url: str = os.getenv("DATABASE_URL", f"sqlite:///{(_BACKEND_DIR / 'storage' / 'app.db').as_posix()}")
    database_echo: bool = _bool_env("DATABASE_ECHO", False)
    feature_live_trading: bool = _bool_env("FEATURE_LIVE_TRADING", False)
    feature_managed_advisory: bool = _bool_env("FEATURE_MANAGED_ADVISORY", False)
    readiness_min_live_score: int = _int_env("READINESS_MIN_LIVE_SCORE", 85)
    audit_export_dir: str = os.getenv("AUDIT_EXPORT_DIR", str(_PROJECT_DIR / "runtime-exports" / "audit")).strip()
    reload: bool = _bool_env("API_RELOAD", True)
    request_logging: bool = _bool_env("REQUEST_LOGGING", True)
    ops_metrics_window_size: int = _int_env("OPS_METRICS_WINDOW_SIZE", 400)
    ops_slow_request_ms: int = _int_env("OPS_SLOW_REQUEST_MS", 800)
    ops_request_timeout_warning_ms: int = _int_env("OPS_REQUEST_TIMEOUT_WARNING_MS", 5000)
    ops_operation_metrics_window_size: int = _int_env("OPS_OPERATION_METRICS_WINDOW_SIZE", 240)
    ops_slow_operation_ms: int = _int_env("OPS_SLOW_OPERATION_MS", 1200)
    ops_upstream_metrics_window_size: int = _int_env("OPS_UPSTREAM_METRICS_WINDOW_SIZE", 180)
    job_worker_enabled: bool = _bool_env("JOB_WORKER_ENABLED", True)
    job_worker_poll_seconds: int = _int_env("JOB_WORKER_POLL_SECONDS", 3)
    job_worker_batch_size: int = _int_env("JOB_WORKER_BATCH_SIZE", 12)
    job_worker_stale_seconds: int = _int_env("JOB_WORKER_STALE_SECONDS", 90)
    trade_automation_worker_enabled: bool = _bool_env(
        "TRADE_AUTOMATION_WORKER_ENABLED",
        os.getenv("ENTERPRISE_RUNTIME_PROFILE", "").strip().lower() not in {"operator-local", "local", "development"},
    )
    trade_automation_worker_desk_scan_batch_size: int = _int_env("TRADE_AUTOMATION_WORKER_DESK_SCAN_BATCH_SIZE", 5)
    trade_automation_deep_analysis_max_workers: int = _int_env("TRADE_AUTOMATION_DEEP_ANALYSIS_MAX_WORKERS", 2)
    trade_automation_deep_analysis_timeout_seconds: int = _int_env("TRADE_AUTOMATION_DEEP_ANALYSIS_TIMEOUT_SECONDS", 20)
    trade_automation_deep_analysis_cache_ttl_seconds: int = _int_env("TRADE_AUTOMATION_DEEP_ANALYSIS_CACHE_TTL_SECONDS", 90)
    trade_automation_deep_analysis_circuit_breaker_seconds: int = _int_env("TRADE_AUTOMATION_DEEP_ANALYSIS_CIRCUIT_BREAKER_SECONDS", 60)
    evidence_accelerator_enabled: bool = _bool_env("EVIDENCE_ACCELERATOR_ENABLED", True)
    evidence_accelerator_aggressive_mode: bool = _bool_env("EVIDENCE_ACCELERATOR_AGGRESSIVE_MODE", True)
    evidence_accelerator_max_events_per_minute: int = _int_env("EVIDENCE_ACCELERATOR_MAX_EVENTS_PER_MINUTE", 5000)
    evidence_accelerator_heartbeat_seconds: int = _int_env("EVIDENCE_ACCELERATOR_HEARTBEAT_SECONDS", 15)
    evidence_accelerator_include_all_desks: bool = _bool_env("EVIDENCE_ACCELERATOR_INCLUDE_ALL_DESKS", True)
    evidence_accelerator_capture_per_gate: bool = _bool_env("EVIDENCE_ACCELERATOR_CAPTURE_PER_GATE", True)
    evidence_accelerator_capture_per_setup: bool = _bool_env("EVIDENCE_ACCELERATOR_CAPTURE_PER_SETUP", True)
    evidence_accelerator_capture_provider_health: bool = _bool_env("EVIDENCE_ACCELERATOR_CAPTURE_PROVIDER_HEALTH", True)
    evidence_accelerator_backoff_duplicate_ratio: float = _float_env("EVIDENCE_ACCELERATOR_BACKOFF_DUPLICATE_RATIO", 0.08)
    evidence_accelerator_backoff_stale_ratio: float = _float_env("EVIDENCE_ACCELERATOR_BACKOFF_STALE_RATIO", 0.10)
    evidence_accelerator_backoff_min_events_per_minute: int = _int_env("EVIDENCE_ACCELERATOR_BACKOFF_MIN_EVENTS_PER_MINUTE", 1500)
    evidence_accelerator_write_lag_warning_ms: int = _int_env("EVIDENCE_ACCELERATOR_WRITE_LAG_WARNING_MS", 2000)
    market_possibility_engine_enabled: bool = _bool_env("MARKET_POSSIBILITY_ENGINE_ENABLED", True)
    market_possibility_scenarios_per_candidate: int = _int_env("MARKET_POSSIBILITY_SCENARIOS_PER_CANDIDATE", 250)
    market_possibility_max_rank_up: float = _float_env("MARKET_POSSIBILITY_MAX_RANK_UP", 4.0)
    market_possibility_max_rank_down: float = _float_env("MARKET_POSSIBILITY_MAX_RANK_DOWN", -8.0)
    market_possibility_counts_toward_live_million: bool = _bool_env("MARKET_POSSIBILITY_COUNTS_TOWARD_LIVE_MILLION", False)
    job_max_attempts: int = _int_env("JOB_MAX_ATTEMPTS", 4)
    job_retry_base_seconds: int = _int_env("JOB_RETRY_BASE_SECONDS", 5)
    job_retry_max_seconds: int = _int_env("JOB_RETRY_MAX_SECONDS", 120)
    partner_webhook_timeout_seconds: int = _int_env("PARTNER_WEBHOOK_TIMEOUT_SECONDS", 5)
    partner_webhook_max_attempts: int = _int_env("PARTNER_WEBHOOK_MAX_ATTEMPTS", 4)
    billing_sync_stale_hours: int = _int_env("BILLING_SYNC_STALE_HOURS", 48)
    billing_recovery_job_max_attempts: int = _int_env("BILLING_RECOVERY_JOB_MAX_ATTEMPTS", 3)
    backup_restore_warning_days: int = _int_env("BACKUP_RESTORE_WARNING_DAYS", 30)
    market_response_cache_ttl_seconds: int = _int_env("MARKET_RESPONSE_CACHE_TTL_SECONDS", 20)
    market_freshness_probe_ticker: str = (os.getenv("MARKET_FRESHNESS_PROBE_TICKER", "SPY").strip().upper() or "SPY")
    market_freshness_probe_interval: str = (os.getenv("MARKET_FRESHNESS_PROBE_INTERVAL", "5m").strip().lower() or "5m")
    market_freshness_warning_multiplier: int = _int_env("MARKET_FRESHNESS_WARNING_MULTIPLIER", 3)
    market_freshness_stale_multiplier: int = _int_env("MARKET_FRESHNESS_STALE_MULTIPLIER", 6)
    market_news_cache_ttl_seconds: int = _int_env("MARKET_NEWS_CACHE_TTL_SECONDS", 900)
    market_news_lookback_days: int = _int_env("MARKET_NEWS_LOOKBACK_DAYS", 5)
    market_news_max_headlines: int = _int_env("MARKET_NEWS_MAX_HEADLINES", 12)
    frontend_snapshot_cache_ttl_seconds: int = _int_env("FRONTEND_SNAPSHOT_CACHE_TTL_SECONDS", 10)
    rate_limit_enabled: bool = _bool_env("RATE_LIMIT_ENABLED", True)
    rate_limit_auth_failure_threshold: int = _int_env("RATE_LIMIT_AUTH_FAILURE_THRESHOLD", 5)
    rate_limit_auth_window_seconds: int = _int_env("RATE_LIMIT_AUTH_WINDOW_SECONDS", 60 * 10)
    rate_limit_auth_lockout_seconds: int = _int_env("RATE_LIMIT_AUTH_LOCKOUT_SECONDS", 60 * 15)
    internal_owned_api_enabled: bool = _bool_env("INTERNAL_OWNED_API_ENABLED", True)
    realtime_stream_enabled: bool = _bool_env("REALTIME_STREAM_ENABLED", True)
    realtime_max_tickers: int = _int_env("REALTIME_MAX_TICKERS", 12)
    internal_stream_poll_seconds: int = _int_env("INTERNAL_STREAM_POLL_SECONDS", 15)
    market_data_adapter: str = os.getenv("MARKET_DATA_ADAPTER", "yfinance").strip().lower() or "yfinance"
    market_data_provider: str = os.getenv("MARKET_DATA_PROVIDER", "free_delayed").strip().lower() or "free_delayed"
    alpaca_market_data_request_timeout_seconds: int = _int_env("ALPACA_MARKET_DATA_REQUEST_TIMEOUT_SECONDS", 10)
    polygon_api_key: str = os.getenv("POLYGON_API_KEY", "").strip()
    polygon_api_base_url: str = os.getenv("POLYGON_API_BASE_URL", "https://api.polygon.io").strip()
    polygon_request_timeout_seconds: int = _int_env("POLYGON_REQUEST_TIMEOUT_SECONDS", 10)
    hybrid_market_state_ttl_seconds: int = _int_env("HYBRID_MARKET_STATE_TTL_SECONDS", 90)
    hybrid_relative_strength_ttl_seconds: int = _int_env("HYBRID_RELATIVE_STRENGTH_TTL_SECONDS", 90)
    hybrid_options_flow_ttl_seconds: int = _int_env("HYBRID_OPTIONS_FLOW_TTL_SECONDS", 180)
    hybrid_event_revision_ttl_seconds: int = _int_env("HYBRID_EVENT_REVISION_TTL_SECONDS", 900)
    broker_mode: str = os.getenv("BROKER_MODE", "internal_paper").strip().lower() or "internal_paper"
    paper_broker_provider: str = os.getenv("PAPER_BROKER_PROVIDER", "internal_paper").strip().lower() or "internal_paper"
    execution_adapter: str = os.getenv("EXECUTION_ADAPTER", "internal_paper").strip().lower() or "internal_paper"
    legitimate_brokerage_api_url: str = os.getenv("LEGITIMATE_BROKERAGE_API_URL", "http://127.0.0.1:8001").strip().rstrip("/")
    legitimate_brokerage_api_key: str = os.getenv("LEGITIMATE_BROKERAGE_API_KEY", "").strip()
    legitimate_brokerage_account_id: str = os.getenv("LEGITIMATE_BROKERAGE_ACCOUNT_ID", "").strip()
    legitimate_brokerage_timeout_seconds: int = _int_env("LEGITIMATE_BROKERAGE_TIMEOUT_SECONDS", 10)
    alpaca_trading_request_timeout_seconds: int = _int_env("ALPACA_TRADING_REQUEST_TIMEOUT_SECONDS", 10)
    alpaca_paper_trading_api_url: str = os.getenv("ALPACA_PAPER_TRADING_API_URL", "https://paper-api.alpaca.markets").strip()
    alpaca_live_trading_api_url: str = os.getenv("ALPACA_LIVE_TRADING_API_URL", "https://api.alpaca.markets").strip()
    alpaca_live_trading_enabled: bool = _bool_env("ALPACA_LIVE_TRADING_ENABLED", False)
    alpaca_api_key_id: str = os.getenv("APCA_API_KEY_ID", os.getenv("ALPACA_API_KEY_ID", "")).strip()
    alpaca_api_secret_key: str = os.getenv("APCA_API_SECRET_KEY", os.getenv("ALPACA_API_SECRET_KEY", "")).strip()
    alpaca_live_api_key_id: str = os.getenv("APCA_LIVE_API_KEY_ID", os.getenv("ALPACA_LIVE_API_KEY_ID", "")).strip()
    alpaca_live_api_secret_key: str = os.getenv(
        "APCA_LIVE_API_SECRET_KEY",
        os.getenv("ALPACA_LIVE_API_SECRET_KEY", ""),
    ).strip()
    alpaca_oauth_client_id: str = os.getenv("ALPACA_OAUTH_CLIENT_ID", "").strip()
    alpaca_oauth_client_secret: str = os.getenv("ALPACA_OAUTH_CLIENT_SECRET", "").strip()
    alpaca_oauth_authorize_url: str = os.getenv("ALPACA_OAUTH_AUTHORIZE_URL", "https://app.alpaca.markets/oauth/authorize").strip()
    alpaca_oauth_token_url: str = os.getenv("ALPACA_OAUTH_TOKEN_URL", "https://api.alpaca.markets/oauth/token").strip()
    alpaca_oauth_redirect_uri: str = os.getenv(
        "ALPACA_OAUTH_REDIRECT_URI",
        f"http://localhost:{_int_env('API_PORT', 8000)}{os.getenv('API_PREFIX', '/api')}/me/brokerage-accounts/alpaca/callback",
    ).strip()
    alpaca_oauth_scope: str = os.getenv("ALPACA_OAUTH_SCOPE", "account:write trading").strip() or "account:write trading"
    alpaca_link_state_cookie_name: str = os.getenv("ALPACA_LINK_STATE_COOKIE_NAME", "stocksignals_alpaca_link_state").strip() or "stocksignals_alpaca_link_state"
    alpaca_link_state_max_age_seconds: int = _int_env("ALPACA_LINK_STATE_MAX_AGE_SECONDS", 60 * 10)
    alpaca_stock_version: str = os.getenv("ALPACA_STOCK_STREAM_VERSION", "v2").strip().lower()
    alpaca_stock_feed: str = os.getenv("ALPACA_STOCK_FEED", "iex").strip().lower()
    alpaca_options_feed: str = os.getenv("ALPACA_OPTIONS_FEED", "opra").strip().lower() or "opra"
    options_broker_provider: str = os.getenv("OPTIONS_BROKER_PROVIDER", "internal").strip().lower() or "internal"
    options_data_provider: str = os.getenv("OPTIONS_DATA_PROVIDER", "free_delayed").strip().lower() or "free_delayed"
    licensed_realtime_options_data: bool = _bool_env("LICENSED_REALTIME_OPTIONS_DATA", False)
    tradier_api_url: str = os.getenv("TRADIER_API_URL", "https://api.tradier.com/v1").strip()
    tradier_sandbox_api_url: str = os.getenv("TRADIER_SANDBOX_API_URL", "https://sandbox.tradier.com/v1").strip()
    tradier_request_timeout_seconds: int = _int_env("TRADIER_REQUEST_TIMEOUT_SECONDS", 10)
    tradier_paper_token: str = os.getenv("TRADIER_PAPER_TOKEN", "").strip()
    tradier_paper_account_id: str = os.getenv("TRADIER_PAPER_ACCOUNT_ID", "").strip()
    tradier_live_token: str = os.getenv("TRADIER_LIVE_TOKEN", "").strip()
    tradier_live_account_id: str = os.getenv("TRADIER_LIVE_ACCOUNT_ID", "").strip()
    options_scan_interval_seconds: int = _int_env("OPTIONS_SCAN_INTERVAL_SECONDS", 30)
    options_quote_max_age_seconds: int = _int_env("OPTIONS_QUOTE_MAX_AGE_SECONDS", 30)
    options_max_spread_pct: float = _float_env("OPTIONS_MAX_SPREAD_PCT", 0.15)
    options_min_volume: int = _int_env("OPTIONS_MIN_VOLUME", 25)
    options_min_open_interest: int = _int_env("OPTIONS_MIN_OPEN_INTEREST", 100)
    options_min_dte_days: int = _int_env("OPTIONS_MIN_DTE_DAYS", 7)
    options_max_dte_days: int = _int_env("OPTIONS_MAX_DTE_DAYS", 45)
    options_scan_candidate_limit: int = _int_env("OPTIONS_SCAN_CANDIDATE_LIMIT", 30)
    options_max_premium_risk_pct: float = _float_env("OPTIONS_MAX_PREMIUM_RISK_PCT", 1.0)
    options_max_open_positions: int = _int_env("OPTIONS_MAX_OPEN_POSITIONS", 4)
    alpaca_use_sandbox: bool = _bool_env("ALPACA_USE_SANDBOX", False)
    alpaca_market_data_ws_url: str = os.getenv("ALPACA_MARKET_DATA_WS_URL", "wss://stream.data.alpaca.markets").strip()
    alpaca_market_data_ws_sandbox_url: str = os.getenv("ALPACA_MARKET_DATA_WS_SANDBOX_URL", "wss://stream.data.sandbox.alpaca.markets").strip()


settings = Settings()
