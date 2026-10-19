"""Import every ORM model so `Base.metadata` is fully populated.

Alembic's autogenerate (see backend/alembic/env.py) relies on this
module having imported all models before it inspects `Base.metadata`.
Add new models to both their own file and this list.
"""

from app.database.base import Base
from app.database.models.audit_log import AuditLog
from app.database.models.backtest import (
    BacktestEquityPoint,
    BacktestRun,
    BacktestTradeRecord,
    ParameterSet,
    StrategyVersion,
)
from app.database.models.broker_credential import BrokerCredential
from app.database.models.instrument import Instrument
from app.database.models.live_trade import LiveTradeRecord
from app.database.models.market_data import (
    BrokerInstrument,
    InstrumentSyncRun,
    MarketCandle,
    MarketTick,
)
from app.database.models.paper_trading import PaperOrderRecord, PaperTradeRecord
from app.database.models.risk_config import RiskConfig
from app.database.models.session import Session
from app.database.models.signal import Signal
from app.database.models.strategy_config import StrategyConfig
from app.database.models.system_event import SystemEvent
from app.database.models.trade_approval import TradeApproval
from app.database.models.trading_account import TradingAccount
from app.database.models.trading_settings import TradingSettings
from app.database.models.user import User

__all__ = [
    "Base",
    "AuditLog",
    "BacktestEquityPoint",
    "BrokerCredential",
    "BacktestRun",
    "BacktestTradeRecord",
    "ParameterSet",
    "StrategyVersion",
    "Instrument",
    "BrokerInstrument",
    "InstrumentSyncRun",
    "LiveTradeRecord",
    "MarketCandle",
    "MarketTick",
    "PaperOrderRecord",
    "PaperTradeRecord",
    "RiskConfig",
    "Session",
    "Signal",
    "StrategyConfig",
    "SystemEvent",
    "TradeApproval",
    "TradingAccount",
    "TradingSettings",
    "User",
]
