# SPDX-FileCopyrightText: 2026 Giovanni Manzoni
# SPDX-License-Identifier: AGPL-3.0-only

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

from app.exceptions import ConflictError


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
    """Turn a ``RequestError`` raised inside a handler into its JSON error response, and a ``ConflictError`` into a 409.

    A ``ConflictError`` can come out of any read or write of a memory (a write that kept losing to other writers,
    replicas that kept holding different versions of a memory, a write whose outcome cannot be told), and
    in those cases the request can be repeated (after a look at the memory when the outcome is unknown), so no route is
    left to answer it with a 500. An ``EmbedderChangedError`` is the exception: repeating the request does not help,
    the server has to be restarted, and its text says so.
    """

    @functools.wraps(handler)
    async def wrapper(request: Request) -> Response:
        try:
            return await handler(request)
        except RequestError as e:
            return JSONResponse({"error": e.message}, status_code=e.status_code)
        except ConflictError as e:
            return JSONResponse({"error": str(e)}, status_code=409)

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


def _is_json_media_type(content_type: str) -> bool:
    """Whether a ``Content-Type`` header names JSON (``application/json`` or an ``application/*+json`` type)."""
    media_type = content_type.split(";", 1)[0].strip().lower()
    return media_type == "application/json" or (
        media_type.startswith("application/") and media_type.endswith("+json")
    )


def _carries_body(request: Request) -> bool:
    """Whether the request declares a body: a non-zero ``Content-Length`` or any ``Transfer-Encoding``."""
    if "transfer-encoding" in request.headers:
        return True
    return request.headers.get("content-length", "0").strip() != "0"


def require_json_content_type(request: Request) -> None:
    """Raise a 415 unless a request that carries a body declares a JSON content type.

    A browser sends ``text/plain``, form and multipart bodies cross-origin without a preflight, and the
    body parsers below read the bytes whatever their type. Insisting on ``application/json`` makes such a
    request impossible to forge from a web page: that content type needs a preflight, which a server
    without CORS (the default) refuses. A request without a body is not affected.
    """
    if _carries_body(request) and not _is_json_media_type(request.headers.get("content-type", "")):
        raise RequestError("Content-Type must be application/json", status_code=415)
