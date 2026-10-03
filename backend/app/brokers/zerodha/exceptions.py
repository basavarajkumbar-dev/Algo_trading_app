"""Typed Kite Connect errors.

They subclass the broker-neutral errors in app.execution.broker, so code
above the Zerodha package can handle them without knowing about Kite:

    KiteError                       base (BrokerError)
    KiteAuthError                   403 / TokenException: log in again (BrokerAuthError)
      KiteTokenExpiredError         the access token is past 06:00 IST or was revoked
    KiteRateLimitError              HTTP 429; the request was not processed
    KiteRejectedError               400-class: input, order, margin, holding, user,
                                    permission errors; carries error_type
    KiteNetworkError                transport errors, timeouts, 5xx, NetworkException;
                                    maybe_processed says whether a write could have
                                    reached Kite (BrokerUnavailableError)
    KiteDataError                   a malformed or unexpected response
    KiteWebSocketError              ticker connection / protocol failure
    KiteOrdersNotAllowedError       an order request on a client that may not place
                                    orders (market data / PAPER execution)

None of these messages ever contains the API secret, access token or
request token.
"""

from __future__ import annotations

from app.execution.broker import (
    BrokerAuthError,
    BrokerError,
    BrokerRateLimitError,
    BrokerUnavailableError,
    LiveOrdersDisabledError,
)


class KiteError(BrokerError):
    pass


class KiteAuthError(BrokerAuthError, KiteError):
    pass


class KiteTokenExpiredError(KiteAuthError):
    pass


class KiteRateLimitError(BrokerRateLimitError, KiteError):
    pass


class KiteRejectedError(KiteError):
    def __init__(self, message: str, error_type: str | None, status_code: int) -> None:
        super().__init__(message)
        self.error_type = error_type
        self.status_code = status_code


class KiteNetworkError(BrokerUnavailableError, KiteError):
    def __init__(self, message: str, *, maybe_processed: bool) -> None:
        super().__init__(message)
        self.maybe_processed = maybe_processed


class KiteDataError(KiteNetworkError):
    """The response could not be understood; treated like a network failure."""

    def __init__(self, message: str) -> None:
        super().__init__(message, maybe_processed=True)


class KiteWebSocketError(KiteError):
    pass


class KiteOrdersNotAllowedError(LiveOrdersDisabledError, KiteError):
    pass
