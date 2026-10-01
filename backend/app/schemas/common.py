"""Shared Pydantic field types.

`pydantic.EmailStr` delegates to `email_validator`, which by default
also performs a live DNS/MX deliverability check. That is inappropriate
here: it makes registration depend on outbound DNS access (which may be
restricted or slow in this sandbox, in CI, or in an air-gapped
deployment) and rejects intentionally reserved/demo domains. `Email`
below validates only the address *format*, which is what input
validation should be responsible for — actual deliverability is
something a verification email step would confirm, not schema
validation.
"""

from __future__ import annotations

from typing import Annotated

from email_validator import EmailNotValidError, validate_email
from pydantic import BeforeValidator


def _validate_email_format(value: str) -> str:
    try:
        info = validate_email(value, check_deliverability=False)
    except EmailNotValidError as exc:
        raise ValueError(str(exc)) from exc
    return info.normalized


Email = Annotated[str, BeforeValidator(_validate_email_format)]
