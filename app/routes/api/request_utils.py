"""Shared request parsing for REST handlers.

Malformed client input (ids that are not UUIDs, invalid or non-object
JSON bodies) must produce a 4xx JSON error rather
than an unhandled exception, which Starlette reports as a 500.

Handlers raise ``RequestError`` through these helpers and are wrapped with
``handle_request_errors`` below the ``custom_route`` decorator.
"""
import functools
import json
import math
from collections.abc import Awaitable, Callable
from typing import Any
from uuid import UUID

from starlette.requests import Request
from starlette.responses import JSONResponse, Response


def _finite_float(token: str) -> float:
    """``json`` hook for number tokens: reject values that overflow to infinity (``1e999``)."""
    number = float(token)
    if not math.isfinite(number):
        raise ValueError(f"non-finite number: {token[:20]}")
    return number


def _reject_constant(token: str) -> float:
    """``json`` hook for the non-standard ``NaN``, ``Infinity`` and ``-Infinity`` literals."""
    raise ValueError(f"non-finite number: {token}")


class RequestError(Exception):
    """A client error that is reported as a JSON ``{"error": message}`` response."""

    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.message = message
        self.status_code = status_code


def handle_request_errors(
    handler: Callable[[Request], Awaitable[Response]],
) -> Callable[[Request], Awaitable[Response]]:
    """Turn a ``RequestError`` raised inside a handler into its JSON error response."""

    @functools.wraps(handler)
    async def wrapper(request: Request) -> Response:
        try:
            return await handler(request)
        except RequestError as e:
            return JSONResponse({"error": e.message}, status_code=e.status_code)

    return wrapper


def path_id(request: Request, name: str) -> UUID:
    """Return a path parameter as a memory id, or raise a 404 if it can never name a resource."""
    raw = request.path_params[name]
    try:
        return UUID(raw)
    except ValueError:
        raise RequestError(f"Invalid {name}: {raw[:50]}. Must be a UUID.", status_code=404) from None


async def read_json_object(request: Request) -> dict[str, Any]:
    """Return the request body as a JSON object, or raise a 400.

    NaN and infinite numbers are rejected: they are not JSON, and a validation error that
    echoed one back could not be rendered as a JSON response (that was a 500).
    """
    try:
        body = json.loads(
            await request.body(), parse_float=_finite_float, parse_constant=_reject_constant,
        )
    except (ValueError, RecursionError):
        raise RequestError("Invalid JSON body") from None
    if not isinstance(body, dict):
        raise RequestError("Request body must be a JSON object")
    return body
