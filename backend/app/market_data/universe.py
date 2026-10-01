"""NIFTY 50 reference universe.

A static snapshot of the NIFTY 50 index constituents, used as:
  1. The instrument list `MockMarketDataProvider.get_instruments()` returns.
  2. Reference/starting prices for the simulated tick generator.

This is a snapshot for demo/seed purposes only — NIFTY 50 constituents
and prices change over time (semi-annual index rebalancing, corporate
actions, daily price movement). It is NOT live market data and must
never be presented as such. Source: NSE-derived public constituent
list, snapshot taken September 2026.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ReferenceInstrument:
    symbol: str
    name: str
    sector: str
    reference_price: float


NIFTY_50_UNIVERSE: tuple[ReferenceInstrument, ...] = (
    ReferenceInstrument("ADANIENT", "Adani Enterprises Ltd.", "Metals & Mining", 3020.0),
    ReferenceInstrument(
        "ADANIPORTS", "Adani Ports and Special Economic Zone Ltd.", "Services", 1824.0
    ),
    ReferenceInstrument("APOLLOHOSP", "Apollo Hospitals Enterprise Ltd.", "Healthcare", 8944.5),
    ReferenceInstrument("ASIANPAINT", "Asian Paints Ltd.", "Consumer Durables", 2451.3),
    ReferenceInstrument("AXISBANK", "Axis Bank Ltd.", "Financial Services", 1257.0),
    ReferenceInstrument("BAJAJ-AUTO", "Bajaj Auto Ltd.", "Automobile and Auto Components", 11414.0),
    ReferenceInstrument("BAJFINANCE", "Bajaj Finance Ltd.", "Financial Services", 1040.3),
    ReferenceInstrument("BAJAJFINSV", "Bajaj Finserv Ltd.", "Financial Services", 1852.5),
    ReferenceInstrument("BEL", "Bharat Electronics Ltd.", "Capital Goods", 393.3),
    ReferenceInstrument("BHARTIARTL", "Bharti Airtel Ltd.", "Telecommunication", 1893.3),
    ReferenceInstrument("CIPLA", "Cipla Ltd.", "Healthcare", 1394.5),
    ReferenceInstrument("COALINDIA", "Coal India Ltd.", "Oil Gas & Consumable Fuels", 409.9),
    ReferenceInstrument("DRREDDY", "Dr. Reddy's Laboratories Ltd.", "Healthcare", 1185.0),
    ReferenceInstrument(
        "EICHERMOT", "Eicher Motors Ltd.", "Automobile and Auto Components", 7516.0
    ),
    ReferenceInstrument("ETERNAL", "Eternal Ltd.", "Consumer Services", 326.85),
    ReferenceInstrument("GRASIM", "Grasim Industries Ltd.", "Construction Materials", 3209.2),
    ReferenceInstrument("HCLTECH", "HCL Technologies Ltd.", "Information Technology", 1249.3),
    ReferenceInstrument("HDFCBANK", "HDFC Bank Ltd.", "Financial Services", 731.0),
    ReferenceInstrument(
        "HDFCLIFE", "HDFC Life Insurance Company Ltd.", "Financial Services", 550.95
    ),
    ReferenceInstrument("HINDALCO", "Hindalco Industries Ltd.", "Metals & Mining", 986.5),
    ReferenceInstrument(
        "HINDUNILVR", "Hindustan Unilever Ltd.", "Fast Moving Consumer Goods", 1932.0
    ),
    ReferenceInstrument("ICICIBANK", "ICICI Bank Ltd.", "Financial Services", 1338.9),
    ReferenceInstrument("ITC", "ITC Ltd.", "Fast Moving Consumer Goods", 262.3),
    ReferenceInstrument("INFY", "Infosys Ltd.", "Information Technology", 1051.4),
    ReferenceInstrument("INDIGO", "InterGlobe Aviation Ltd.", "Services", 4920.0),
    ReferenceInstrument("JSWSTEEL", "JSW Steel Ltd.", "Metals & Mining", 1275.6),
    ReferenceInstrument("JIOFIN", "Jio Financial Services Ltd.", "Financial Services", 229.89),
    ReferenceInstrument("KOTAKBANK", "Kotak Mahindra Bank Ltd.", "Financial Services", 412.5),
    ReferenceInstrument("LT", "Larsen & Toubro Ltd.", "Construction", 3885.0),
    ReferenceInstrument(
        "M&M", "Mahindra & Mahindra Ltd.", "Automobile and Auto Components", 3052.0
    ),
    ReferenceInstrument(
        "MARUTI", "Maruti Suzuki India Ltd.", "Automobile and Auto Components", 12103.0
    ),
    ReferenceInstrument("MAXHEALTH", "Max Healthcare Institute Ltd.", "Healthcare", 1049.3),
    ReferenceInstrument("NTPC", "NTPC Ltd.", "Power", 323.65),
    ReferenceInstrument("NESTLEIND", "Nestle India Ltd.", "Fast Moving Consumer Goods", 1372.4),
    ReferenceInstrument(
        "ONGC", "Oil & Natural Gas Corporation Ltd.", "Oil Gas & Consumable Fuels", 232.8
    ),
    ReferenceInstrument("POWERGRID", "Power Grid Corporation of India Ltd.", "Power", 270.3),
    ReferenceInstrument(
        "RELIANCE", "Reliance Industries Ltd.", "Oil Gas & Consumable Fuels", 1226.4
    ),
    ReferenceInstrument("SBILIFE", "SBI Life Insurance Company Ltd.", "Financial Services", 1731.0),
    ReferenceInstrument("SHRIRAMFIN", "Shriram Finance Ltd.", "Financial Services", 1010.0),
    ReferenceInstrument("SBIN", "State Bank of India", "Financial Services", 996.2),
    ReferenceInstrument("SUNPHARMA", "Sun Pharmaceutical Industries Ltd.", "Healthcare", 1837.3),
    ReferenceInstrument("TCS", "Tata Consultancy Services Ltd.", "Information Technology", 2105.0),
    ReferenceInstrument(
        "TATACONSUM", "Tata Consumer Products Ltd.", "Fast Moving Consumer Goods", 996.0
    ),
    ReferenceInstrument(
        "TMPV", "Tata Motors Passenger Vehicles Ltd.", "Automobile and Auto Components", 303.8
    ),
    ReferenceInstrument("TATASTEEL", "Tata Steel Ltd.", "Metals & Mining", 185.54),
    ReferenceInstrument("TECHM", "Tech Mahindra Ltd.", "Information Technology", 1537.1),
    ReferenceInstrument("TITAN", "Titan Company Ltd.", "Consumer Durables", 4798.5),
    ReferenceInstrument("TRENT", "Trent Ltd.", "Consumer Services", 2824.0),
    ReferenceInstrument("ULTRACEMCO", "UltraTech Cement Ltd.", "Construction Materials", 11014.0),
    ReferenceInstrument("WIPRO", "Wipro Ltd.", "Information Technology", 166.83),
)

# A synthetic composite "index" symbol the mock provider also streams,
# representing the NIFTY 50 index level itself (distinct from any single
# constituent). The baseline is an illustrative, roughly-current index
# level for simulation purposes only — never treated as a live quote.
NIFTY_INDEX_SYMBOL = "NIFTY50"
NIFTY_INDEX_BASELINE = 25000.0

assert len(NIFTY_50_UNIVERSE) == 50, "NIFTY_50_UNIVERSE must contain exactly 50 instruments"
assert len({i.symbol for i in NIFTY_50_UNIVERSE}) == 50, "Duplicate symbol in NIFTY_50_UNIVERSE"
