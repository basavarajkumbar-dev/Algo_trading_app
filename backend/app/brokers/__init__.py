"""Broker adapters beyond the paper simulator (Phase 11).

    mock.py      MockBrokerAdapter: scriptable broker with fault injection
    zerodha/     ZerodhaBrokerAdapter: Kite Connect v3
    service.py   BrokerService: picks and supervises the live broker

Only the order manager (app.execution.order_manager) and the broker service
talk to these. Strategy, signal, scanner, regime and risk code must not
import this package (enforced by app/tests/test_broker_architecture.py).
"""
