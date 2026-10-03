"""Backtest request and result models."""

from __future__ import annotations

from datetime import date, datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, field_validator, model_validator

from app.analytics.performance import EquityPoint
from app.backtesting.metrics import BACKTEST_NOTE, BacktestMetrics, RobustnessReport
from app.backtesting.parameters import StrategyChoice
from app.execution.costs import CostModel

MAX_SYMBOLS = 20
MAX_DAYS = 250


class BacktestMode(str, Enum):
    HISTORICAL = "HISTORICAL"
    OUT_OF_SAMPLE = "OUT_OF_SAMPLE"
    WALK_FORWARD = "WALK_FORWARD"


class DataSourceKind(str, Enum):
    SYNTHETIC = "synthetic"
    CSV = "csv"
    ZERODHA = "zerodha"  # real 5-minute candles from Kite Connect (needs a login)


class DataSourceSpec(BaseModel):
    kind: DataSourceKind = DataSourceKind.SYNTHETIC
    seed: int = Field(default=42, ge=0, le=2**31)


class RiskSpec(BaseModel):
    risk_per_trade_percent: float = Field(default=1.0, gt=0, le=5)
    daily_loss_limit_percent: float = Field(default=3.0, gt=0, le=20)
    max_trades_per_day: int = Field(default=5, ge=1, le=50)
    max_consecutive_losses: int = Field(default=3, ge=1, le=50)
    max_position_value: float = Field(default=50_000.0, gt=0)
    max_exposure: float = Field(default=50_000.0, gt=0)
    margin_multiplier: float = Field(default=1.0, ge=1, le=5)
    allow_short: bool = True


class CostSpec(BaseModel):
    slippage_bps: float = Field(default=5.0, ge=0, le=200)
    brokerage_rate: float = Field(default=0.0003, ge=0)
    brokerage_cap_per_order: float = Field(default=20.0, ge=0)
    stt_sell_rate: float = Field(default=0.00025, ge=0)
    exchange_rate: float = Field(default=0.0000297, ge=0)
    sebi_per_crore: float = Field(default=10.0, ge=0)
    stamp_buy_rate: float = Field(default=0.00003, ge=0)
    gst_rate: float = Field(default=0.18, ge=0)

    def cost_model(self) -> CostModel:
        return CostModel(**self.model_dump(exclude={"slippage_bps"}))


class ValidationSpec(BaseModel):
    oos_fraction: float = Field(default=0.3, ge=0.1, le=0.6)
    wf_train_days: int = Field(default=20, ge=5, le=200)
    wf_test_days: int = Field(default=5, ge=2, le=60)
    wf_anchored: bool = False  # anchored: training always starts at the first day
    min_trades_for_selection: int = Field(default=8, ge=2, le=200)
    null_control: bool = False  # also run on a no-edge synthetic dataset


class BacktestRequest(BaseModel):
    name: str | None = Field(default=None, max_length=120)
    mode: BacktestMode = BacktestMode.HISTORICAL
    strategy: StrategyChoice = StrategyChoice.ALL
    version: str | None = None  # if given, must match the version the parameters produce
    symbols: list[str] = Field(min_length=1, max_length=MAX_SYMBOLS)
    start_date: date
    end_date: date
    timeframe: str = "5m"
    parameters: dict[str, Any] = Field(default_factory=dict)
    grid: dict[str, list[Any]] | None = None
    capital: float = Field(default=100_000.0, ge=10_000, le=100_000_000)
    risk: RiskSpec = Field(default_factory=RiskSpec)
    costs: CostSpec = Field(default_factory=CostSpec)
    validation: ValidationSpec = Field(default_factory=ValidationSpec)
    data_source: DataSourceSpec = Field(default_factory=DataSourceSpec)
    warmup_days: int = Field(default=15, ge=8, le=60)

    @field_validator("symbols")
    @classmethod
    def _unique_upper(cls, value: list[str]) -> list[str]:
        out: list[str] = []
        for s in value:
            u = s.strip().upper()
            if u and u not in out:
                out.append(u)
        return out

    @field_validator("timeframe")
    @classmethod
    def _timeframe(cls, value: str) -> str:
        if value not in ("5m", "15m"):
            raise ValueError("timeframe must be 5m or 15m (entry timeframe)")
        return value

    @model_validator(mode="after")
    def _dates(self) -> BacktestRequest:
        if self.end_date < self.start_date:
            raise ValueError("end_date is before start_date")
        if (self.end_date - self.start_date).days > MAX_DAYS * 7 // 5 + 7:
            raise ValueError(f"date range too long (about {MAX_DAYS} trading days at most)")
        return self


class Segment(BaseModel):
    label: str  # "In-sample", "Out-of-sample", "Window 3 train" ...
    kind: str  # FULL | IN_SAMPLE | OUT_OF_SAMPLE | TRAIN | TEST
    start: date
    end: date
    days: int
    params_hash: str
    params: dict[str, Any]
    objective: float | None = None
    metrics: BacktestMetrics


class GridResult(BaseModel):
    params_hash: str
    params: dict[str, Any]
    varied: dict[str, Any]  # just the grid keys
    objective: float | None  # lower 1-SE bound of mean R (None: too few trades)
    smoothed: float | None  # averaged with grid neighbours (stability)
    trades: int
    net_pnl: float
    profit_factor: float | None
    expectancy_r: float | None
    selected: bool = False
    window: str = "full"


class WalkForwardSummary(BaseModel):
    windows: int
    profitable_windows: int
    efficiency: float | None  # mean test expectancy R / mean train expectancy R
    parameter_changes: int  # how often the selected parameter set changed
    note: str


class BacktestTrade(BaseModel):
    id: str
    segment: str
    symbol: str
    side: str
    strategy: str | None
    strategy_version: str | None
    regime: str | None
    score: float | None
    decision_time: datetime | None
    opened_at: datetime | None
    closed_at: datetime | None
    quantity: int
    entry_price: float | None
    exit_price: float | None
    stop_loss: float
    trailing_stop: float | None
    target: float
    exit_reason: str | None
    gross_pnl: float
    charges: float
    net_pnl: float
    r_multiple: float | None
    planned_rr: float | None
    reasons: list[str] = Field(default_factory=list)


class DailyEquity(BaseModel):
    date: date
    equity: float


class BacktestOutcome(BaseModel):
    mode: BacktestMode
    primary_label: str  # what the headline metrics describe
    metrics: BacktestMetrics
    robustness: RobustnessReport
    equity_curve: list[EquityPoint]
    daily_equity: list[DailyEquity]
    trades: list[BacktestTrade]
    segments: list[Segment]
    grid: list[GridResult] = Field(default_factory=list)
    combinations_tested: int = 1
    selected_params: dict[str, Any]
    selected_params_hash: str
    walk_forward: WalkForwardSummary | None = None
    in_sample_metrics: BacktestMetrics | None = None
    degradation: float | None = None  # OOS expectancy R / IS expectancy R
    null_control: BacktestMetrics | None = None
    strategy_versions: list[dict[str, Any]] = Field(default_factory=list)
    data: dict[str, Any] = Field(default_factory=dict)
    replay: dict[str, Any] = Field(default_factory=dict)
    execution: dict[str, Any] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    note: str = BACKTEST_NOTE
