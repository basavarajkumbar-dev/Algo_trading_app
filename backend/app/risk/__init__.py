"""Risk engine (Phase 8).

Every order the paper trading engine places passes through the
RiskManager first. Limits are configuration (see app/risk/config.py and
the RISK_* environment variables), not fixed truths: a trade that
passes every check can still lose money.
"""
