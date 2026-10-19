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

from pydantic import AliasChoices, Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.core.constants import ExecutionMode, MarketDataMode, TradingMode
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
    # APPROVAL mode: how long a proposed trade waits for a decision. An expired
    # request is never executed.
    approval_ttl_seconds: int = Field(default=120, ge=15, le=3600, alias="APPROVAL_TTL_SECONDS")
    paper_capital: float = Field(default=100_000.0, alias="PAPER_CAPITAL")
    risk_per_trade: float = Field(default=1.0, alias="RISK_PER_TRADE")
    max_daily_loss: float = Field(default=3.0, alias="MAX_DAILY_LOSS")
    max_trades_per_day: int = Field(default=5, alias="MAX_TRADES_PER_DAY")

    # --- Broker (Zerodha) — optional, app must run without these --------
    # Secrets are SecretStr: they never appear in repr(), logs or API output.
    # KITE_* names (Phase 13) and the Phase 11 ZERODHA_* names are both accepted.
    zerodha_api_key: str | None = Field(
        default=None, validation_alias=AliasChoices("KITE_API_KEY", "ZERODHA_API_KEY")
    )
    zerodha_api_secret: SecretStr | None = Field(
        default=None, validation_alias=AliasChoices("KITE_API_SECRET", "ZERODHA_API_SECRET")
    )
    zerodha_access_token: SecretStr | None = Field(
        default=None, validation_alias=AliasChoices("KITE_ACCESS_TOKEN", "ZERODHA_ACCESS_TOKEN")
    )
    # User-entered broker credentials (API key / secret saved from the app) are
    # encrypted with this key before they reach the database. 32 random bytes,
    # urlsafe base64 (`python -m app.scripts.generate_credentials_key`). The
    # key may instead be read from a file (Docker secret). Unset: credentials
    # cannot be saved (no plaintext fallback). Previous keys still decrypt
    # during a rotation (comma-separated).
    credentials_encryption_key: SecretStr | None = Field(
        default=None, alias="CREDENTIALS_ENCRYPTION_KEY"
    )
    credentials_encryption_key_file: str | None = Field(
        default=None, alias="CREDENTIALS_ENCRYPTION_KEY_FILE"
    )
    credentials_encryption_previous_keys: SecretStr | None = Field(
        default=None, alias="CREDENTIALS_ENCRYPTION_PREVIOUS_KEYS"
    )
    # Must equal the redirect URL registered for the Kite Connect app; point it
    # at GET /api/v1/broker/zerodha/callback.
    kite_redirect_url: str | None = Field(default=None, alias="KITE_REDIRECT_URL")
    # Where the browser goes after the Kite login callback (the frontend).
    frontend_url: str = Field(default="http://localhost:3000", alias="FRONTEND_URL")
    zerodha_api_base_url: str = Field(
        default="https://api.kite.trade",
        validation_alias=AliasChoices("KITE_API_BASE_URL", "ZERODHA_API_BASE_URL"),
    )
    zerodha_login_url: str = Field(
        default="https://kite.zerodha.com/connect/login",
        validation_alias=AliasChoices("KITE_LOGIN_URL", "ZERODHA_LOGIN_URL"),
    )
    kite_ws_url: str = Field(default="wss://ws.kite.trade", alias="KITE_WS_URL")

    # --- Market data source and execution (Phase 13) ------------------------
    # MOCK: simulated feed. ZERODHA: Kite Connect live data. Unset: ZERODHA
    # when Kite credentials are configured, otherwise MOCK.
    market_data_mode: MarketDataMode | None = Field(default=None, alias="MARKET_DATA_MODE")
    # PAPER: simulated fills. ZERODHA: real orders, which additionally need
    # LIVE_TRADING_ENABLED=true and the contract section 9 conditions; this
    # build has no live order path, so ZERODHA still executes on paper.
    execution_mode: ExecutionMode = Field(default=ExecutionMode.PAPER, alias="EXECUTION_MODE")
    live_trading_enabled: bool = Field(default=False, alias="LIVE_TRADING_ENABLED")
    # --- Live safety layer (Phase 14) -------------------------------------
    # Live orders additionally need an admin's explicit arming action (with
    # a typed confirmation and their password); arming lasts until the end
    # of the trading day, a disarm, the kill switch, a restart or a logout.
    live_max_active_positions: int = Field(
        default=1, ge=1, le=1, alias="LIVE_MAX_ACTIVE_POSITIONS"
    )  # one active trade; values above 1 are refused in this build
    live_entry_timeout_seconds: float = Field(
        default=60.0, gt=0, le=900, alias="LIVE_ENTRY_TIMEOUT_SECONDS"
    )
    live_max_position_value: float = Field(
        default=25_000.0, gt=0, alias="LIVE_MAX_POSITION_VALUE"
    )  # rupees per trade, also capped by the available margin
    live_max_order_quantity: int = Field(default=5_000, ge=1, alias="LIVE_MAX_ORDER_QUANTITY")
    live_max_stop_percent: float = Field(
        default=3.0, gt=0, le=20, alias="LIVE_MAX_STOP_PERCENT"
    )  # stop farther than this from entry is refused
    live_min_risk_reward: float = Field(default=1.0, gt=0, le=10, alias="LIVE_MIN_RISK_REWARD")
    live_signal_max_age_seconds: float = Field(
        default=120.0, gt=0, le=3600, alias="LIVE_SIGNAL_MAX_AGE_SECONDS"
    )
    live_close_positions_on_kill: bool = Field(
        default=False, alias="LIVE_CLOSE_POSITIONS_ON_KILL"
    )  # the kill switch never closes positions unless this or the request says so
    live_poll_seconds: float = Field(default=2.0, gt=0.2, le=30, alias="LIVE_POLL_SECONDS")
    # A feed (or symbol) with no tick for this long is STALE: no executable
    # signal and no new order from it.
    data_stale_threshold_seconds: float = Field(
        default=10.0, gt=0, le=600, alias="DATA_STALE_THRESHOLD_SECONDS"
    )
    default_timeframe: Timeframe = Field(default=Timeframe.M5, alias="DEFAULT_TIMEFRAME")
    candle_timeframes: list[Timeframe] = Field(
        default_factory=lambda: [
            Timeframe.M1,
            Timeframe.M3,
            Timeframe.M5,
            Timeframe.M15,
            Timeframe.M30,
            Timeframe.M60,
        ],
        alias="CANDLE_TIMEFRAMES",
    )
    # Kite ticker mode for equities: quote (default, no depth) or full (depth
    # and exchange timestamps). Indices are always subscribed in full mode,
    # which adds their exchange timestamp (used for the latency figure).
    kite_tick_mode: str = Field(
        default="quote", pattern="^(ltp|quote|full)$", alias="KITE_TICK_MODE"
    )
    # Instrument master: exchanges to download, and whether to sync at login.
    instrument_exchanges: list[str] = Field(
        default_factory=lambda: ["NSE", "NFO"], alias="INSTRUMENT_EXCHANGES"
    )
    instrument_sync_on_login: bool = Field(default=True, alias="INSTRUMENT_SYNC_ON_LOGIN")
    # Largest accepted move between consecutive ticks of one instrument.
    tick_max_move_percent: float = Field(default=20.0, gt=0, le=100, alias="TICK_MAX_MOVE_PERCENT")
    persist_candles: bool = Field(default=True, alias="PERSIST_CANDLES")
    persist_raw_ticks: bool = Field(default=False, alias="PERSIST_RAW_TICKS")
    # Market orders and SL-M need market protection (Zerodha rejects 0 since
    # 1 April 2026). -1 = Zerodha's automatic protection; 0 < x <= 100 = custom %.
    zerodha_market_protection: float = Field(
        default=-1, ge=-1, le=100, alias="ZERODHA_MARKET_PROTECTION"
    )
    # Client-side order rate cap; Zerodha enforces 10/s per account without
    # an exchange-registered strategy.
    zerodha_orders_per_second: int = Field(
        default=8, ge=1, le=10, alias="ZERODHA_ORDERS_PER_SECOND"
    )
    zerodha_timeout_seconds: float = Field(
        default=10.0, gt=0, le=60, alias="ZERODHA_TIMEOUT_SECONDS"
    )
    broker_health_interval_seconds: float = Field(
        default=30.0, ge=5, le=600, alias="BROKER_HEALTH_INTERVAL_SECONDS"
    )
    broker_reconcile_interval_seconds: float = Field(
        default=10.0, ge=2, le=600, alias="BROKER_RECONCILE_INTERVAL_SECONDS"
    )

    @field_validator("zerodha_market_protection")
    @classmethod
    def _market_protection_not_zero(cls, value: float) -> float:
        if value == 0:
            raise ValueError("ZERODHA_MARKET_PROTECTION cannot be 0 (Zerodha rejects it)")
        return value

    @field_validator("zerodha_api_key", "zerodha_api_secret", "zerodha_access_token", mode="before")
    @classmethod
    def _blank_is_unset(cls, value: object) -> object:
        return None if isinstance(value, str) and not value.strip() else value

    @property
    def zerodha_configured(self) -> bool:
        """API key and secret present (an access token can come from a login later)."""

        return bool(self.zerodha_api_key and self.zerodha_api_secret)

    @property
    def effective_market_data_mode(self) -> MarketDataMode:
        """MARKET_DATA_MODE, or ZERODHA when Kite credentials exist, else MOCK."""

        if self.market_data_mode is not None:
            return self.market_data_mode
        return MarketDataMode.ZERODHA if self.zerodha_configured else MarketDataMode.MOCK

    @property
    def effective_execution_mode(self) -> ExecutionMode:
        """PAPER unless EXECUTION_MODE=ZERODHA *and* LIVE_TRADING_ENABLED=true.
        Credentials alone never change it."""

        if self.execution_mode == ExecutionMode.ZERODHA and self.live_trading_enabled:
            return ExecutionMode.ZERODHA
        return ExecutionMode.PAPER

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

    # --- Universes (scanner and backtests) --------------------------------
    # Comma-separated universe ids (app.universe.registry), e.g.
    # "NIFTY50" (default, unchanged), "NIFTY500", "NIFTY_BANK,NIFTY_MIDCAP150",
    # "NSE_ALL_EQ". Applied at startup; the watchlist is the NIFTY 50 index
    # plus the resolved symbols.
    scanner_universes: str = Field(default="NIFTY50", alias="SCANNER_UNIVERSES")
    # Capacity limit for the live watchlist. Symbols over it are excluded
    # with an explicit reason shown by the scanner (never silently).
    scanner_max_symbols: int = Field(default=200, ge=1, le=3000, alias="SCANNER_MAX_SYMBOLS")
    # Most symbols one backtest may process (was a fixed 20).
    backtest_max_symbols: int = Field(default=50, ge=1, le=2000, alias="BACKTEST_MAX_SYMBOLS")
    universe_cache_dir: str = Field(default="/tmp/gainzalgo-universes", alias="UNIVERSE_CACHE_DIR")
    universe_max_age_days: float = Field(default=7.0, gt=0, alias="UNIVERSE_MAX_AGE_DAYS")

    # --- Mobile app -------------------------------------------------------------
    # Deep-link scheme the Zerodha callback returns to for logins started
    # from the Android app (must match the app's "scheme" in app.json).
    mobile_app_scheme: str = Field(
        default="gainzalgo", pattern="^[a-z][a-z0-9+.-]*$", alias="MOBILE_APP_SCHEME"
    )

    # --- Strategies -----------------------------------------------------------
    # Added strategies to enable in the live/paper router (off by default):
    # comma-separated from RANGE_TRADING, BREAKOUT_TRADING, VOLUME_TRADING,
    # MA_CROSSOVER, or "ALL". The three original strategies are unaffected.
    strategy_extra_enabled: str = Field(default="", alias="STRATEGY_EXTRA_ENABLED")

    # --- API access ---------------------------------------------------------
    # Require a signed-in user for every data endpoint (market, signals,
    # trading, analytics, backtests, ...) and the market WebSocket. Unset:
    # required when APP_ENV is production or staging, open in development
    # (the existing default for local use).
    api_require_auth: bool | None = Field(default=None, alias="API_REQUIRE_AUTH")
    # Self-service sign-up (POST /auth/register, role TRADER). Unset: open in
    # development, closed in production/staging, where accounts are created
    # with `python -m app.scripts.create_user`.
    allow_registration: bool | None = Field(default=None, alias="ALLOW_REGISTRATION")

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
    def strategy_extra_ids(self) -> list[str]:
        names = [n.strip().upper() for n in self.strategy_extra_enabled.split(",") if n.strip()]
        if "ALL" in names:
            return ["RANGE_TRADING", "BREAKOUT_TRADING", "VOLUME_TRADING", "MA_CROSSOVER"]
        return names

    @property
    def scanner_universe_ids(self) -> list[str]:
        return [u.strip().upper() for u in self.scanner_universes.split(",") if u.strip()]

    def deployment_problems(self) -> list[str]:
        """Reasons this configuration must not serve a production or staging
        deployment. Empty in development."""
        if self.app_env.lower() not in ("production", "staging"):
            return []
        problems: list[str] = []
        for name, value in (("APP_SECRET", self.app_secret), ("JWT_SECRET", self.jwt_secret)):
            if "insecure" in value or "change-me" in value or len(value) < 32:
                problems.append(f"{name} is a placeholder or shorter than 32 characters")
        if not self.effective_api_require_auth:
            problems.append("API_REQUIRE_AUTH=false is not allowed outside development")
        return problems

    @property
    def effective_allow_registration(self) -> bool:
        if self.allow_registration is not None:
            return self.allow_registration
        return self.app_env.lower() not in ("production", "staging")

    @property
    def effective_api_require_auth(self) -> bool:
        if self.api_require_auth is not None:
            return self.api_require_auth
        return self.app_env.lower() in ("production", "staging")

    @property
    def is_live_trading_possible(self) -> bool:
        """Single-source check combining the two static config conditions
        required (of several — see PROJECT_CONTRACT.md section 9) before
        LIVE mode may ever be considered. This is a necessary, NOT a
        sufficient, condition. Runtime checks (risk engine, broker
        connection, system health, session window) are added in the
        phases that implement them and must ALSO pass.
        """

        # Phase 14: the explicit live configuration is EXECUTION_MODE=ZERODHA
        # together with LIVE_TRADING_ENABLED=true. Still not sufficient: an
        # admin must arm live trading and every pre-trade check must pass.
        return self.effective_execution_mode == ExecutionMode.ZERODHA


@lru_cache
def get_settings() -> Settings:
    """Cached settings accessor used as a FastAPI dependency."""

    return Settings()
