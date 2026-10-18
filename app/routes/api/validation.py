"""Shared request validation helpers for the REST API routes.

Keeps the way routes report invalid input (400 responses) and the bounds they
enforce on client supplied integers in one place.
"""
from typing import Any

from pydantic import ValidationError
from starlette.responses import JSONResponse

from app.routes.api.query_params import INT32_MAX, INT32_MIN, INT64_MAX, INT64_MIN

# Integers outside the database column are rejected by the driver (OverflowError on
# SQLite, DataError on PostgreSQL) and would surface as 500s. Counts and limits are
# bound as BIGINT (int64); row ids are INTEGER columns on PostgreSQL (int32).


def validation_error_response(error: ValidationError) -> JSONResponse:
    """Build the 400 response for a pydantic ``ValidationError``.

    ``ValidationError.errors()`` is not safe to hand to ``JSONResponse`` as is:
    errors raised by custom validators carry the exception object in ``ctx`` and
    non-finite floats (``NaN``/``Infinity``, accepted by ``json.loads``) are echoed in
    ``input``; both make JSON rendering raise and the client get a 500 instead.
    Only the documented ``type``, ``loc`` and ``msg`` keys are returned.
    """
    return JSONResponse(
        {
            "error": error.errors(
                include_url=False,
                include_context=False,
                include_input=False,
            ),
        },
        status_code=400,
    )


def parse_int_query(
    raw: str, name: str, *, min_value: int = INT64_MIN, max_value: int = INT64_MAX,
) -> int:
    """Parse the text of an integer query parameter.

    Raises:
        ValueError: With a client-facing message if the text is not an integer
            or the integer is outside ``min_value``..``max_value`` (int64 by default)
    """
    try:
        value = int(raw)
    except ValueError:
        raise ValueError(f"Invalid {name}: {raw[:50]}. Must be an integer.") from None
    if not min_value <= value <= max_value:
        raise ValueError(f"Invalid {name}: {raw[:50]}. Value is out of range.")
    return value


def parse_optional_int_query(params: Any, name: str) -> int | None:
    """Parse an optional integer query parameter; missing or empty means ``None``.

    Raises:
        ValueError: As for ``parse_int_query``
    """
    raw = params.get(name)
    if not raw:
        return None
    return parse_int_query(raw, name)


def parse_optional_id_query(params: Any, name: str) -> int | None:
    """Parse an optional row-id query parameter (``project_id``); empty means ``None``.

    Row ids are INTEGER columns on PostgreSQL, so the accepted range is int32.

    Raises:
        ValueError: As for ``parse_int_query``
    """
    raw = params.get(name)
    if not raw:
        return None
    return parse_int_query(raw, name, min_value=INT32_MIN, max_value=INT32_MAX)


def validate_body_id(value: Any, name: str) -> int:
    """Validate an identifier taken from a JSON request body.

    JSON permits any value type and arbitrarily large integers; anything that is not
    an integer fitting the INTEGER id columns (32 bits) would otherwise reach the
    database driver and fail there with a 500.

    Raises:
        ValueError: With a client-facing message if the value is not a valid id
    """
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{name} must be an integer")
    if not INT32_MIN <= value <= INT32_MAX:
        raise ValueError(f"{name} is out of range")
    return value
