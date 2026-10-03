"""A fake Kite Connect v3 server for tests (httpx.MockTransport).

It follows the documented request/response shapes: form-encoded bodies,
X-Kite-Version: 3, `Authorization: token api_key:access_token`, JSON
envelopes {status, data} / {status: "error", message, error_type}, CSV
instruments. The test drives the "exchange" (set_status) and injects
faults. Nothing here talks to the network.
"""

from __future__ import annotations

import gzip
import itertools
import json
from collections.abc import Callable
from typing import Any
from urllib.parse import parse_qs

import httpx

API_KEY = "testkey123"
API_SECRET = "s3cr3t-api-secret-value"
ACCESS_TOKEN = "tok-very-secret-access-token"
REQUEST_TOKEN = "req-token-abc123"

INSTRUMENTS_CSV = (
    "instrument_token,exchange_token,tradingsymbol,name,last_price,expiry,strike,"
    "tick_size,lot_size,instrument_type,segment,exchange\n"
    "738561,2885,RELIANCE,RELIANCE INDUSTRIES,0,,0,0.1,1,EQ,NSE,NSE\n"
    "2953217,11536,TCS,TATA CONSULTANCY SERV LT,0,,0,0.1,1,EQ,NSE,NSE\n"
)


# A richer master for Phase 13 tests: cash, indices, futures and options.
MASTER_CSV = (
    "instrument_token,exchange_token,tradingsymbol,name,last_price,expiry,strike,"
    "tick_size,lot_size,instrument_type,segment,exchange\n"
    "738561,2885,RELIANCE,RELIANCE INDUSTRIES,0,,0,0.1,1,EQ,NSE,NSE\n"
    "2953217,11536,TCS,TATA CONSULTANCY SERV LT,0,,0,0.1,1,EQ,NSE,NSE\n"
    "408065,1594,INFY,INFOSYS,0,,0,0.1,1,EQ,NSE,NSE\n"
    "256265,1001,NIFTY 50,NIFTY 50,0,,0,0,0,EQ,INDICES,NSE\n"
    "260105,1016,NIFTY BANK,NIFTY BANK,0,,0,0,0,EQ,INDICES,NSE\n"
    "13238786,51714,NIFTY26OCTFUT,NIFTY,0,2026-10-27,0,0.1,75,FUT,NFO-FUT,NFO\n"
    "13239042,51715,NIFTY26NOVFUT,NIFTY,0,2026-11-24,0,0.1,75,FUT,NFO-FUT,NFO\n"
    "10560770,41253,NIFTY2610625000CE,NIFTY,0,2026-10-06,25000,0.05,75,CE,NFO-OPT,NFO\n"
    "10561026,41254,NIFTY2610625000PE,NIFTY,0,2026-10-06,25000,0.05,75,PE,NFO-OPT,NFO\n"
    "10561282,41255,NIFTY2610625100CE,NIFTY,0,2026-10-06,25100,0.05,75,CE,NFO-OPT,NFO\n"
    "bad,row,MISSING,,,,,,,,,NSE\n"
)


class FakeKite:
    def __init__(self, *, token: str | None = ACCESS_TOKEN) -> None:
        self.valid_token = token
        self.orders: dict[str, dict[str, Any]] = {}
        self.history: dict[str, list[dict[str, Any]]] = {}
        self.requests: list[tuple[str, str, dict[str, Any], dict[str, str]]] = []
        self._ids = itertools.count(250930000000001)
        # fault injection
        self.connect_error_next = 0  # httpx.ConnectError before processing
        self.timeout_after_processing_next = False  # process, then ReadTimeout
        self.status_next: list[tuple[int, dict[str, Any]]] = []  # forced responses
        self.down = False  # every request: ConnectError
        self.initial_status = "OPEN"  # what a new order becomes
        # Phase 13 market data
        self.instruments_csv = INSTRUMENTS_CSV
        self.ltp: dict[str, tuple[int, float]] = {}  # "NSE:INFY" -> (token, price)
        self.candles: dict[tuple[int, str], list[list[Any]]] = {}  # (token, interval) -> rows
        self.historical_calls: list[tuple[int, str, str, str]] = []
        self.trades: list[dict[str, Any]] = []
        self.login_time = "2026-10-01 09:00:00"  # IST, as Kite sends it

    # --- helpers -----------------------------------------------------------------

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handle)

    def posts(self, path_prefix: str = "/orders/") -> list[dict[str, Any]]:
        return [
            form
            for method, path, form, _ in self.requests
            if method == "POST" and path.startswith(path_prefix)
        ]

    def set_status(
        self,
        order_id: str,
        status: str,
        *,
        filled: int | None = None,
        average: float | None = None,
        message: str | None = None,
    ) -> None:
        o = self.orders[order_id]
        o["status"] = status
        if filled is not None:
            o["filled_quantity"] = filled
            o["pending_quantity"] = o["quantity"] - filled
            if average is not None:
                o["average_price"] = average
        if status == "CANCELLED":
            o["cancelled_quantity"] = o["quantity"] - o["filled_quantity"]
            o["pending_quantity"] = 0
        if message is not None:
            o["status_message"] = message
        self.history[order_id].append(dict(o))

    def add_external(self, symbol: str = "INFY") -> str:
        oid = str(next(self._ids))
        o = self._new_order(
            oid,
            {
                "tradingsymbol": symbol,
                "exchange": "NSE",
                "transaction_type": "BUY",
                "order_type": "LIMIT",
                "quantity": "1",
                "product": "CNC",
                "price": "1500",
            },
        )
        o["tag"] = None
        return oid

    # --- server ---------------------------------------------------------------------

    @staticmethod
    def _ok(data: Any) -> httpx.Response:
        return httpx.Response(200, json={"status": "success", "data": data})

    @staticmethod
    def _err(code: int, error_type: str, message: str) -> httpx.Response:
        return httpx.Response(
            code, json={"status": "error", "message": message, "error_type": error_type}
        )

    def handle(self, request: httpx.Request) -> httpx.Response:
        method, path = request.method, request.url.path
        form = (
            {k: v[0] for k, v in parse_qs(request.content.decode()).items()}
            if request.content
            else {}
        )
        self.requests.append((method, path, form, dict(request.headers)))
        self._query = parse_qs(
            request.url.query.decode()
            if isinstance(request.url.query, bytes)
            else str(request.url.query)
        )
        if self.down or self.connect_error_next > 0:
            self.connect_error_next = max(0, self.connect_error_next - 1)
            raise httpx.ConnectError("connection refused", request=request)
        if self.status_next:
            code, body = self.status_next.pop(0)
            return httpx.Response(code, json=body)
        if request.headers.get("X-Kite-Version") != "3":
            return self._err(400, "InputException", "missing X-Kite-Version")
        if path == "/session/token" and method == "POST":
            return self._session(form)
        if path == "/session/token" and method == "DELETE":
            return self._ok(True)
        if (
            request.headers.get("Authorization") != f"token {API_KEY}:{self.valid_token}"
            or not self.valid_token
        ):
            return self._err(403, "TokenException", "Incorrect `api_key` or `access_token`.")
        route = self._route(method, path)
        if route is None:
            return self._err(404, "GeneralException", "Route not found")
        response = route(path, form)
        if self.timeout_after_processing_next and method != "GET":
            self.timeout_after_processing_next = False
            raise httpx.ReadTimeout("read timed out", request=request)
        return response

    def _route(
        self, method: str, path: str
    ) -> Callable[[str, dict[str, Any]], httpx.Response] | None:
        if method == "GET" and path == "/user/profile":
            return lambda *_: self._ok(
                {
                    "user_id": "AB1234",
                    "user_name": "Test User",
                    "user_shortname": "Test",
                    "email": "t@example.com",
                    "broker": "ZERODHA",
                    "exchanges": ["NSE", "BSE"],
                    "products": ["CNC", "MIS"],
                    "order_types": ["MARKET", "LIMIT", "SL", "SL-M"],
                }
            )
        if method == "GET" and path == "/user/margins/equity":
            return lambda *_: self._ok(
                {
                    "enabled": True,
                    "net": 98000.5,
                    "available": {
                        "cash": 100000.0,
                        "opening_balance": 100000.0,
                        "live_balance": 99000.0,
                    },
                    "utilised": {"debits": 1000.0, "m2m_realised": -120.0, "m2m_unrealised": 45.5},
                }
            )
        if method == "GET" and path == "/portfolio/positions":
            return lambda *_: self._ok(
                {
                    "net": [
                        {
                            "tradingsymbol": "RELIANCE",
                            "exchange": "NSE",
                            "product": "MIS",
                            "quantity": -10,
                            "average_price": 1400.5,
                            "last_price": 1395.0,
                            "realised": 0,
                            "unrealised": 55.0,
                        }
                    ],
                    "day": [],
                }
            )
        if method == "GET" and path == "/orders":
            return lambda *_: self._ok(list(self.orders.values()))
        if method == "GET" and path == "/trades":
            return lambda *_: self._ok(list(self.trades))
        if method == "GET" and path.startswith("/orders/") and path.endswith("/trades"):
            return lambda p, _: self._ok(
                [t for t in self.trades if t["order_id"] == p.split("/")[2]]
            )
        if method == "GET" and path.startswith("/orders/"):
            return lambda p, _: self._ok(self.history.get(p.rsplit("/", 1)[1], []))
        if method == "POST" and path == "/orders/regular":
            return lambda _, form: self._place(form)
        if method == "PUT" and path.startswith("/orders/regular/"):
            return lambda p, form: self._modify(p.rsplit("/", 1)[1], form)
        if method == "DELETE" and path.startswith("/orders/regular/"):
            return lambda p, _: self._cancel(p.rsplit("/", 1)[1])
        if method == "GET" and path.startswith("/instruments/historical/"):
            return lambda p, _: self._historical(p)
        if method == "GET" and path.startswith("/instruments"):
            return lambda p, _: self._instruments(p)
        if method == "GET" and path in ("/quote/ltp", "/quote", "/quote/ohlc"):
            return lambda *_: self._quote()
        return None

    # the current request's query string (set by handle())
    _query: dict[str, list[str]] = {}

    def _instruments(self, path: str) -> httpx.Response:
        parts = path.strip("/").split("/")
        body = self.instruments_csv
        if len(parts) == 2:  # /instruments/:exchange
            lines = body.splitlines()
            keep = [lines[0]] + [ln for ln in lines[1:] if ln.endswith("," + parts[1])]
            body = "\n".join(keep) + "\n"
        return httpx.Response(
            200, content=gzip.compress(body.encode()), headers={"Content-Type": "text/csv"}
        )

    def _quote(self) -> httpx.Response:
        out: dict[str, Any] = {}
        for key in self._query.get("i", []):
            if key in self.ltp:
                token, price = self.ltp[key]
                out[key] = {"instrument_token": token, "last_price": price}
        return self._ok(out)

    def _historical(self, path: str) -> httpx.Response:
        from datetime import datetime as _dt

        _, _, token, interval = path.strip("/").split("/")
        start = self._query.get("from", [""])[0]
        end = self._query.get("to", [""])[0]
        self.historical_calls.append((int(token), interval, start, end))
        fmt = "%Y-%m-%d %H:%M:%S"
        a, b = _dt.strptime(start, fmt), _dt.strptime(end, fmt)
        rows = []
        for row in self.candles.get((int(token), interval), []):
            try:
                ts = _dt.strptime(str(row[0])[:19], "%Y-%m-%dT%H:%M:%S")
            except ValueError:
                rows.append(row)  # malformed rows are returned as-is (client must cope)
                continue
            if a <= ts <= b:
                rows.append(row)
        return self._ok({"candles": rows})

    def _session(self, form: dict[str, Any]) -> httpx.Response:
        import hashlib

        expected = hashlib.sha256(f"{API_KEY}{REQUEST_TOKEN}{API_SECRET}".encode()).hexdigest()
        if form.get("request_token") != REQUEST_TOKEN or form.get("checksum") != expected:
            return self._err(403, "TokenException", "Token is invalid or has expired.")
        self.valid_token = ACCESS_TOKEN
        return self._ok(
            {
                "user_id": "AB1234",
                "user_name": "Test User",
                "user_shortname": "Test",
                "access_token": ACCESS_TOKEN,
                "refresh_token": "",
                "public_token": "pub",
                "login_time": self.login_time,
                "exchanges": ["NSE"],
                "products": ["MIS"],
                "order_types": ["MARKET", "LIMIT", "SL-M"],
            }
        )

    def _new_order(self, oid: str, form: dict[str, Any]) -> dict[str, Any]:
        o = {
            "order_id": oid,
            "exchange_order_id": "1100000000" + oid[-5:],
            "status": "PUT ORDER REQ RECEIVED",
            "tradingsymbol": form["tradingsymbol"],
            "exchange": form["exchange"],
            "transaction_type": form["transaction_type"],
            "order_type": form["order_type"],
            "product": form["product"],
            "validity": form.get("validity", "DAY"),
            "variety": "regular",
            "quantity": int(form["quantity"]),
            "filled_quantity": 0,
            "pending_quantity": int(form["quantity"]),
            "cancelled_quantity": 0,
            "price": float(form.get("price") or 0),
            "trigger_price": float(form.get("trigger_price") or 0),
            "average_price": 0,
            "tag": form.get("tag"),
            "status_message": None,
            "order_timestamp": "2026-10-01 10:00:00",
            "exchange_timestamp": None,
        }
        self.orders[oid] = o
        self.history[oid] = [dict(o)]
        return o

    def _place(self, form: dict[str, Any]) -> httpx.Response:
        if form["order_type"] in ("MARKET", "SL-M") and str(form.get("market_protection", "0")) in (
            "0",
            "0.0",
        ):
            return self._err(
                400, "InputException", "Market orders without market protection are not allowed."
            )
        oid = str(next(self._ids))
        o = self._new_order(oid, form)
        status = (
            "TRIGGER PENDING"
            if form["order_type"] == "SL-M" and self.initial_status == "OPEN"
            else self.initial_status
        )
        o["status"] = status
        if status == "REJECTED":
            o["status_message"] = "RMS:Margin Exceeds"
        self.history[oid].append(dict(o))
        return self._ok({"order_id": oid})

    def _modify(self, oid: str, form: dict[str, Any]) -> httpx.Response:
        o = self.orders.get(oid)
        if o is None or o["status"] in ("COMPLETE", "CANCELLED", "REJECTED"):
            return self._err(400, "OrderException", "Order cannot be modified.")
        for k in ("quantity", "price", "trigger_price"):
            if k in form:
                o[k] = float(form[k]) if k != "quantity" else int(form[k])
        o["status"] = "TRIGGER PENDING" if o["order_type"] == "SL-M" else "OPEN"
        self.history[oid].append(dict(o))
        return self._ok({"order_id": oid})

    def _cancel(self, oid: str) -> httpx.Response:
        o = self.orders.get(oid)
        if o is None or o["status"] in ("COMPLETE", "CANCELLED", "REJECTED"):
            return self._err(400, "OrderException", "Order cannot be cancelled.")
        self.set_status(oid, "CANCELLED")
        return self._ok({"order_id": oid})


def dumps(value: Any) -> str:
    return json.dumps(value, default=str)
