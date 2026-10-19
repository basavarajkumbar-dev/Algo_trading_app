"""Architecture rules:

    STRATEGY -> SIGNAL -> RISK -> ORDER MANAGER -> BROKER INTERFACE -> ZERODHA

* Strategy, signal, scanner, regime, intelligence, price-action and risk
  code never import a broker adapter, the Kite client, httpx or the
  kiteconnect SDK.
* Only the Zerodha package talks HTTP to Kite.
* No credential value is committed: .env.example leaves them empty and
  no source file contains a Zerodha secret-looking assignment.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

APP = Path(__file__).resolve().parents[1]
REPO = APP.parents[1]

UPSTREAM = [
    "strategies",
    "signals",
    "scanner",
    "regime",
    "market_intelligence",
    "price_action",
    "indicators",
    "risk",
]
FORBIDDEN = (
    "app.brokers",
    "app.execution.order_manager",
    "httpx",
    "kiteconnect",
    "requests",
    "websockets",
)


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text())
    out: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            out.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            out.add(node.module)
    return out


def test_strategy_signal_and_risk_code_never_reach_a_broker() -> None:
    offenders = []
    for pkg in UPSTREAM:
        for path in (APP / pkg).rglob("*.py") if (APP / pkg).exists() else []:
            for name in _imports(path):
                if name.startswith(FORBIDDEN):
                    offenders.append(f"{path.relative_to(APP)} imports {name}")
    assert offenders == []


def test_only_the_zerodha_package_uses_http() -> None:
    users = sorted(
        str(p.relative_to(APP))
        for p in APP.rglob("*.py")
        if "tests" not in p.parts and any(n.startswith("httpx") for n in _imports(p))
    )
    # universe/loader.py downloads the official NSE Indices constituent files
    # (not a broker); every broker call still goes through the Zerodha package.
    assert users == [
        "brokers/zerodha/adapter.py",
        "brokers/zerodha/kite_client.py",
        "universe/loader.py",
    ]


def test_only_the_kite_ticker_opens_websockets_to_the_broker() -> None:
    users = sorted(
        str(p.relative_to(APP))
        for p in APP.rglob("*.py")
        if "tests" not in p.parts and any(n.startswith("websockets") for n in _imports(p))
    )
    assert users == ["brokers/zerodha/ticker.py"]


def test_market_data_layer_does_not_depend_on_the_broker() -> None:
    """Engines read candles from MarketDataService; the Zerodha provider
    plugs into it from the brokers package, never the other way round."""

    offenders = [
        f"{p.relative_to(APP)} imports {name}"
        for p in (APP / "market_data").rglob("*.py")
        for name in _imports(p)
        if name.startswith("app.brokers")
    ]
    assert offenders == []


def test_no_credentials_are_committed() -> None:
    env = (REPO / ".env.example").read_text()
    # users enter their own Kite credentials in the app: the template has no
    # credential values (and no longer even an empty KITE_API_KEY= line)
    for key in (
        "KITE_API_KEY",
        "KITE_API_SECRET",
        "KITE_ACCESS_TOKEN",
        "ZERODHA_API_KEY",
        "ZERODHA_API_SECRET",
        "ZERODHA_ACCESS_TOKEN",
        "CREDENTIALS_ENCRYPTION_KEY",
    ):
        for line in (ln for ln in env.splitlines() if ln.startswith(f"{key}=")):
            assert line == f"{key}=", line
    assert "CREDENTIALS_ENCRYPTION_KEY=" in env.splitlines()
    pattern = re.compile(
        r"(api_secret|access_token|request_token|ZERODHA_API_SECRET|ZERODHA_ACCESS_TOKEN|KITE_API_SECRET|KITE_ACCESS_TOKEN)\s*=\s*[\"'][A-Za-z0-9]{12,}[\"']"
    )
    hits = [
        str(p.relative_to(REPO))
        for p in APP.rglob("*.py")
        if "tests" not in p.parts and pattern.search(p.read_text())
    ]
    assert hits == []
    ignored = (REPO / ".gitignore").read_text()
    assert ".env" in ignored.split()
    assert "LIVE_TRADING_ENABLED=false" in env.splitlines()
    assert "EXECUTION_MODE=PAPER" in env.splitlines()
