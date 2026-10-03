"""Zerodha Kite Connect v3 adapter (Phase 11). See adapter.py for the
safety rules; credentials come only from environment variables."""

from app.brokers.zerodha.adapter import ZerodhaBrokerAdapter, zerodha_from_settings

__all__ = ["ZerodhaBrokerAdapter", "zerodha_from_settings"]
