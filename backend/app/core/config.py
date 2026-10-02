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

from datetime import date, time
from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.core.constants import TradingMode
from app.market_data.schemas import Timeframe
from app.market_data.universe import NIFTY_50_UNIVERSE, NIFTY_INDEX_SYMBOL
from app.risk.config import SessionMode


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

    # --- Market data (Phase 3, extended in Phase 4) ------------------------
    market_data_tick_interval_seconds: float = Field(
        default=1.0, alias="MARKET_DATA_TICK_INTERVAL_SECONDS"
    )
    # Default: the simulated NIFTY50 index plus all 50 constituents, so
    # breadth and relative strength cover the whole universe.
    market_data_default_watchlist: list[str] = Field(
        default_factory=lambda: [NIFTY_INDEX_SYMBOL, *(i.symbol for i in NIFTY_50_UNIVERSE)]
    )
    # Closed candles of simulated warm-up history seeded per symbol and
    # timeframe at startup (0 disables). 250 covers the 200-period MA.
    market_data_warmup_candles: int = Field(
        default=250, ge=0, le=2000, alias="MARKET_DATA_WARMUP_CANDLES"
    )
    # How often the mock provider re-rolls simulated regimes (seconds).
    # 0 disables rotation.
    market_data_regime_rotation_seconds: float = Field(
        default=900.0, ge=0, alias="MARKET_DATA_REGIME_ROTATION_SECONDS"
    )

    # --- Market intelligence (Phase 4) ----------------------------------
    # Timeframe the regime detector and engines analyse by default.
    intelligence_timeframe: Timeframe = Field(default=Timeframe.M5, alias="INTELLIGENCE_TIMEFRAME")
    # How often (seconds) the intelligence service recomputes results.
    intelligence_refresh_seconds: float = Field(
        default=5.0, gt=0, alias="INTELLIGENCE_REFRESH_SECONDS"
    )

    # --- Price action (Phase 5) ------------------------------------------
    # Multi-timeframe roles. Must satisfy context > setup > entry.
    price_action_context_timeframe: Timeframe = Field(
        default=Timeframe.M60, alias="PRICE_ACTION_CONTEXT_TIMEFRAME"
    )
    price_action_setup_timeframe: Timeframe = Field(
        default=Timeframe.M15, alias="PRICE_ACTION_SETUP_TIMEFRAME"
    )
    price_action_entry_timeframe: Timeframe = Field(
        default=Timeframe.M5, alias="PRICE_ACTION_ENTRY_TIMEFRAME"
    )
    # How often (seconds) the price action service recomputes results.
    price_action_refresh_seconds: float = Field(
        default=15.0, gt=0, alias="PRICE_ACTION_REFRESH_SECONDS"
    )

    # --- Signals and scanner (Phase 7) ----------------------------------
    # Score at or above which a signal is READY (executable candidate).
    signal_executable_threshold: float = Field(
        default=85.0, ge=0, le=100, alias="SIGNAL_EXECUTABLE_THRESHOLD"
    )
    # Optional JSON object overriding scoring weights, e.g.
    # {"market_alignment": 15, "volume": 10}. Unlisted components keep
    # their defaults (the brief's initial model).
    signal_score_weights: dict[str, float] | None = Field(
        default=None, alias="SIGNAL_SCORE_WEIGHTS"
    )
    # Candidates scoring below this are shown by the scanner but not stored.
    signal_store_min_score: float = Field(
        default=70.0, ge=0, le=100, alias="SIGNAL_STORE_MIN_SCORE"
    )
    scanner_top_n: int = Field(default=10, ge=1, le=50, alias="SCANNER_TOP_N")

    # --- Risk engine (Phase 8) --------------------------------------------
    # RISK_PER_TRADE, MAX_DAILY_LOSS and MAX_TRADES_PER_DAY above are the
    # percentage / count limits; these complete the RiskManager's set.
    risk_max_consecutive_losses: int = Field(default=3, ge=1, alias="RISK_MAX_CONSECUTIVE_LOSSES")
    risk_max_position_value: float = Field(default=50_000.0, gt=0, alias="RISK_MAX_POSITION_VALUE")
    risk_max_exposure: float = Field(default=50_000.0, gt=0, alias="RISK_MAX_EXPOSURE")
    risk_lot_size: int = Field(default=1, ge=1, alias="RISK_LOT_SIZE")
    risk_tick_size: float = Field(default=0.05, gt=0, alias="RISK_TICK_SIZE")
    risk_max_order_quantity: int = Field(default=100_000, ge=1, alias="RISK_MAX_ORDER_QUANTITY")
    risk_margin_multiplier: float = Field(default=1.0, ge=1, le=5, alias="RISK_MARGIN_MULTIPLIER")
    risk_max_data_age_seconds: float = Field(default=30.0, gt=0, alias="RISK_MAX_DATA_AGE_SECONDS")
    risk_max_signal_age_seconds: float = Field(
        default=120.0, gt=0, alias="RISK_MAX_SIGNAL_AGE_SECONDS"
    )
    risk_max_entry_deviation_r: float = Field(
        default=0.25, ge=0, le=1, alias="RISK_MAX_ENTRY_DEVIATION_R"
    )
    risk_allow_short: bool = Field(default=True, alias="RISK_ALLOW_SHORT")
    # EXCHANGE: NSE hours in IST, Mon-Fri. SIMULATED_24H: every moment is
    # in-session, for exercising the simulated feed outside market hours.
    trading_session_mode: SessionMode = Field(
        default=SessionMode.EXCHANGE, alias="TRADING_SESSION_MODE"
    )
    trading_last_entry_time: time = Field(default=time(15, 0), alias="TRADING_LAST_ENTRY_TIME")
    trading_square_off_time: time = Field(default=time(15, 15), alias="TRADING_SQUARE_OFF_TIME")
    # Exchange holidays (JSON list of ISO dates); none are built in.
    trading_holidays: list[date] = Field(default_factory=list, alias="TRADING_HOLIDAYS")

    # --- Paper broker (Phase 8) ---------------------------------------------
    # Execution assumptions. Paper results depend on them.
    paper_slippage_bps: float = Field(default=5.0, ge=0, le=500, alias="PAPER_SLIPPAGE_BPS")
    paper_execution_delay_seconds: float = Field(
        default=0.0, ge=0, le=60, alias="PAPER_EXECUTION_DELAY_SECONDS"
    )
    paper_max_fill_fraction: float = Field(default=1.0, gt=0, le=1, alias="PAPER_MAX_FILL_FRACTION")
    paper_entry_timeout_seconds: float = Field(
        default=60.0, gt=0, alias="PAPER_ENTRY_TIMEOUT_SECONDS"
    )
    # Whether an emergency stop also closes the active position when the
    # request does not say. Off by default: positions are not auto-closed.
    paper_emergency_close_positions: bool = Field(
        default=False, alias="PAPER_EMERGENCY_CLOSE_POSITIONS"
    )
    # --- Backtesting (Phase 10) --------------------------------------------
    # Directory of real 5-minute CSV bars (see docs/backtesting.md). Unset:
    # only the synthetic history is available.
    backtest_data_dir: str | None = Field(default=None, alias="BACKTEST_DATA_DIR")
    # Worker processes for the signal replay (default: CPU count).
    backtest_workers: int | None = Field(default=None, ge=1, le=32, alias="BACKTEST_WORKERS")
    # Optional JSON object overriding cost-model fields, e.g.
    # {"brokerage_cap_per_order": 20, "stt_sell_rate": 0.00025}.
    paper_costs: dict[str, float] | None = Field(default=None, alias="PAPER_COSTS")

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
