"""Application configuration.

All configuration is read from environment variables (see .env.example
at the repository root). Nothing here should ever be hardcoded to a real
secret or credential — see PROJECT_CONTRACT.md section 8 (Security
Requirements).

The trading-mode default is intentionally PAPER and cannot be changed by
merely editing code in this file without also satisfying the multiple
independent conditions described in PROJECT_CONTRACT.md section 9 in the
phases that implement live trading. Phase 1 does not implement any order
execution at all — this setting exists now only so later phases have a
single, typed source of truth.
"""

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.core.constants import TradingMode


class Settings(BaseSettings):
    """Typed, environment-driven application settings."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # --- Core application -------------------------------------------------
    app_env: str = Field(default="development", alias="APP_ENV")
    app_name: str = Field(default="GAINZALGO PRO MAX")
    app_secret: str = Field(default="dev-insecure-secret-change-me", alias="APP_SECRET")
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")

    # --- Demo mode ----------------------------------------------------------
    # When true, the platform is expected to run entirely on mock market
    # data and paper trading, with no external credentials. Phase 1 does
    # not yet implement market data or trading, but the flag is defined
    # here so every later phase reads the same source of truth.
    demo_mode: bool = Field(default=True, alias="DEMO_MODE")

    # --- Database / cache -----------------------------------------------
    database_url: str = Field(
        default="postgresql+asyncpg://gainzalgo:gainzalgo@postgres:5432/gainzalgo",
        alias="DATABASE_URL",
    )
    redis_url: str = Field(default="redis://redis:6379/0", alias="REDIS_URL")

    # --- Auth (wired up starting Phase 3; defined now for consistency) --
    jwt_secret: str = Field(default="dev-insecure-jwt-secret-change-me", alias="JWT_SECRET")
    jwt_algorithm: str = Field(default="HS256")
    access_token_expire_minutes: int = Field(default=30)
    refresh_token_expire_minutes: int = Field(default=60 * 24 * 7)

    # --- Trading safety defaults (not enforced until later phases) ------
    trading_mode: TradingMode = Field(default=TradingMode.PAPER, alias="TRADING_MODE")
    live_mode_enabled: bool = Field(default=False, alias="LIVE_MODE_ENABLED")
    paper_capital: float = Field(default=100_000.0, alias="PAPER_CAPITAL")
    risk_per_trade: float = Field(default=1.0, alias="RISK_PER_TRADE")
    max_daily_loss: float = Field(default=3.0, alias="MAX_DAILY_LOSS")
    max_trades_per_day: int = Field(default=5, alias="MAX_TRADES_PER_DAY")

    # --- Broker (Zerodha) — optional, app must run without these --------
    zerodha_api_key: str | None = Field(default=None, alias="ZERODHA_API_KEY")
    zerodha_api_secret: str | None = Field(default=None, alias="ZERODHA_API_SECRET")
    zerodha_access_token: str | None = Field(default=None, alias="ZERODHA_ACCESS_TOKEN")

    # --- Market data (Phase 3) -------------------------------------------
    market_data_tick_interval_seconds: float = Field(
        default=1.0, alias="MARKET_DATA_TICK_INTERVAL_SECONDS"
    )
    market_data_default_watchlist: list[str] = Field(
        default_factory=lambda: [
            "NIFTY50",
            "RELIANCE",
            "TCS",
            "HDFCBANK",
            "ICICIBANK",
            "INFY",
            "ITC",
            "SBIN",
            "BHARTIARTL",
            "LT",
            "TATASTEEL",
        ]
    )

    # --- CORS -------------------------------------------------------------
    cors_allow_origins: list[str] = Field(
        default_factory=lambda: [
            "http://localhost:3000",
            "http://127.0.0.1:3000",
        ]
    )

    @property
    def is_live_trading_possible(self) -> bool:
        """Single-source check combining the two static config conditions
        required (of several — see PROJECT_CONTRACT.md section 9) before
        LIVE mode may ever be considered. This is a necessary, NOT a
        sufficient, condition. Runtime checks (risk engine, broker
        connection, system health, session window) are added in the
        phases that implement them and must ALSO pass.
        """

        return self.trading_mode == TradingMode.LIVE and self.live_mode_enabled


@lru_cache
def get_settings() -> Settings:
    """Cached settings accessor used as a FastAPI dependency."""

    return Settings()
