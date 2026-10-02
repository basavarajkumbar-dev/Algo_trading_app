"""Transaction cost model for NSE intraday (MIS) equity.

The default rates approximate a discount broker's published intraday
equity charges. They are ASSUMPTIONS for paper accounting, not quotes:
statutory rates change, so check them against your broker's current
charge sheet and override them through configuration.

    brokerage   min(₹20, 0.03% of turnover) per executed order
    STT         0.025% of turnover, sell side
    exchange    0.00297% of turnover (NSE transaction charge)
    SEBI fee    ₹10 per crore of turnover
    stamp duty  0.003% of turnover, buy side
    GST         18% of (brokerage + exchange + SEBI fee)

Brokerage is capped per order, so a partially filled order is charged
on its cumulative turnover and never more than the cap in total.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from app.execution.models import ChargeBreakdown, Side


class CostModel(BaseModel):
    brokerage_rate: float = Field(default=0.0003, ge=0)
    brokerage_cap_per_order: float = Field(default=20.0, ge=0)
    stt_sell_rate: float = Field(default=0.00025, ge=0)
    exchange_rate: float = Field(default=0.0000297, ge=0)
    sebi_per_crore: float = Field(default=10.0, ge=0)
    stamp_buy_rate: float = Field(default=0.00003, ge=0)
    gst_rate: float = Field(default=0.18, ge=0)

    def fill_charges(
        self, *, side: Side, turnover: float, brokerage_already_charged: float = 0.0
    ) -> ChargeBreakdown:
        """Charges for one fill of `turnover` rupees."""

        brokerage = min(
            turnover * self.brokerage_rate,
            max(self.brokerage_cap_per_order - brokerage_already_charged, 0.0),
        )
        stt = turnover * self.stt_sell_rate if side is Side.SELL else 0.0
        exchange = turnover * self.exchange_rate
        sebi = turnover * self.sebi_per_crore / 1e7
        stamp = turnover * self.stamp_buy_rate if side is Side.BUY else 0.0
        gst = (brokerage + exchange + sebi) * self.gst_rate
        return ChargeBreakdown(
            brokerage=round(brokerage, 4),
            stt=round(stt, 4),
            exchange=round(exchange, 4),
            sebi=round(sebi, 4),
            stamp_duty=round(stamp, 4),
            gst=round(gst, 4),
        )

    def round_trip_estimate(self, *, entry_side: Side, quantity: int, price: float) -> float:
        """Approximate charges for entering and exiting at `price`."""

        turnover = quantity * price
        entry = self.fill_charges(side=entry_side, turnover=turnover)
        exit_ = self.fill_charges(side=entry_side.opposite, turnover=turnover)
        return round(entry.total + exit_.total, 2)
