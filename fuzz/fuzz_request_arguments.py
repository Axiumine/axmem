# SPDX-FileCopyrightText: 2026 Giovanni Manzoni
# SPDX-License-Identifier: AGPL-3.0-only

"""Fuzz the coercion of the arguments that a client sends to a memory tool or a REST list route.

A client picks the type of every argument: ``limit`` can be ``"7"``, ``7.0``, ``true``, ``[]``
or a string of five thousand digits. Each coercion must return a value of the type it promises,
inside the bounds it promises, or refuse with ``ValueError`` (which a route answers with a 400);
any other exception would reach the client as a server error.

    uv run python fuzz/fuzz_request_arguments.py -max_total_time=60
"""
from datetime import UTC, timedelta
from typing import Any
from uuid import UUID

import atheris
from shared import invariant, isolated_settings, json_value, run

with isolated_settings(), atheris.instrument_imports(include=["app"]):
    from fastmcp.exceptions import ToolError

    from app.routes.api.query_params import parse_int_param
    from app.routes.mcp.adapters.arguments import (
        clamp_int,
        coerce_bool,
        coerce_int,
        coerce_memory_id,
        coerce_memory_ids,
        coerce_str_list,
    )
    from app.routes.mcp.adapters.memory_arguments import parse_created_since

UUID_TEXT = "12345678-1234-5678-1234-567812345678"
# The branch of ``argument`` that sends a float (0 is text, 1 an integer, 3 any JSON)
FLOAT_CHOICE = 2
# Arguments worth trying early: text a client would send for a number, a boolean or an id, and
# what is near it.
TEXTS = [
    "7", " 7 ", "-1", "1_0", "1e3", "7.0", "0x10", "٣", "true", "False", " YES ", "off", "0", "1",
    "", UUID_TEXT, f"  {UUID_TEXT}\n", f"{{{UUID_TEXT}}}", f"urn:uuid:{UUID_TEXT}",
    UUID_TEXT.replace("-", ""), UUID_TEXT[:-1],
]
# Timestamps for ``created_since``: the edges of the years 1 to 9999, where moving a local time to UTC leaves the range
# (``0001-01-01T00:00:00+01:00`` raised ``OverflowError`` out of ``parse_created_since``), and what is near a valid one.
TIMESTAMPS = [
    "2026-10-18T12:30:45Z", "2026-10-18T12:30:45+02:00", "2026-10-18T12:30:45.250-05:30",
    "0001-01-01T00:00:00Z", "0001-01-01T00:00:00+01:00", "0001-01-01T00:00:00-01:00",
    "9999-12-31T23:59:59.999999Z", "9999-12-31T23:59:59-01:00", "9999-12-31T23:59:59+01:00",
    "2026-10-18", "2026-10-18T12:30:45", "2026-10-18 12:30:45+00:00", "2026-13-45T12:30:45Z", "yesterday",
]


def argument(provider: Any) -> Any:
    """What a client may send for one argument: prepared text, an integer, a float or any JSON."""
    choice = provider.ConsumeIntInRange(0, 3)
    if choice == 0:
        return provider.PickValueInList(TEXTS)
    if choice == 1:
        return provider.ConsumeInt(8) * 10 ** provider.ConsumeIntInRange(0, 30)  # past 64 bits
    if choice == FLOAT_CHOICE:
        return provider.ConsumeFloat()
    return json_value(provider)


def is_exactly_int(value: Any) -> bool:
    """An ``int`` that is not a ``bool`` (``True`` is an ``int`` in Python, not a number here)."""
    return isinstance(value, int) and not isinstance(value, bool)


def check_integers(provider: Any) -> None:
    value = argument(provider)
    low = provider.ConsumeIntInRange(-1000, 1000)
    high = low + provider.ConsumeIntInRange(0, 1000)
    try:
        number = coerce_int(value, "limit")
        clamped = clamp_int(value, "limit", low, high)
    except ValueError:  # coerce_int refuses what clamp_int refuses, so one refusal covers both
        return
    invariant(is_exactly_int(number), f"coerce_int gave a {type(number).__name__}")
    invariant(
        not is_exactly_int(value) or number == value,
        f"coerce_int changed the integer {value} to {number}",
    )
    invariant(low <= clamped <= high, f"clamp_int gave {clamped} outside {low}..{high}")


def check_query_parameter(provider: Any) -> None:
    """The REST routes read a query parameter as text, so this one is given text only."""
    text = provider.PickValueInList(TEXTS) if provider.ConsumeBool() else provider.ConsumeUnicodeNoSurrogates(24)
    low = provider.ConsumeIntInRange(-100, 100)
    high = low + provider.ConsumeIntInRange(0, 100)
    default = provider.ConsumeIntInRange(low, high)
    params = {"limit": text} if provider.ConsumeBool() else {}
    try:
        number = parse_int_param(params, "limit", default, min_value=low, max_value=high)
    except ValueError:
        invariant("limit" in params, "an absent parameter was refused")
        return
    invariant(low <= number <= high, f"parse_int_param gave {number} outside {low}..{high}")
    invariant("limit" in params or number == default, "an absent parameter lost its default")


def check_booleans(provider: Any) -> None:
    try:
        flag = coerce_bool(argument(provider), "flag")
    except ValueError:
        return
    invariant(isinstance(flag, bool), f"coerce_bool gave a {type(flag).__name__}")


def check_memory_ids(provider: Any) -> None:
    try:
        memory_id = coerce_memory_id(argument(provider), "memory_id")
    except ValueError:
        memory_id = None
    if memory_id is not None:
        invariant(isinstance(memory_id, UUID), f"coerce_memory_id gave a {type(memory_id)}")
        invariant(
            coerce_memory_id(str(memory_id)) == memory_id,
            f"{memory_id} does not read back as itself",
        )
    many = [argument(provider) for _ in range(provider.ConsumeIntInRange(0, 4))]
    try:
        ids = coerce_memory_ids(many, "memory_ids")
    except ValueError:
        return
    invariant(
        len(ids) == len(many) and all(isinstance(item, UUID) for item in ids),
        "coerce_memory_ids lost an id",
    )


def check_text_lists(provider: Any) -> None:
    value = argument(provider)
    try:
        items = coerce_str_list(value, "tags")
    except ValueError:
        return
    invariant(
        items is None or all(isinstance(item, str) for item in items),
        "coerce_str_list gave an item that is not text",
    )
    invariant(not isinstance(value, str) or items == [value], "a bare text is not a list of one")


def timestamp(provider: Any) -> str:
    """An ISO 8601 timestamp built from fields, some of them past their range."""
    year = provider.PickValueInList([0, 1, 1970, 2026, 9998, 9999, provider.ConsumeIntInRange(0, 9999)])
    date = f"{year:04d}-{provider.ConsumeIntInRange(0, 13):02d}-{provider.ConsumeIntInRange(0, 32):02d}"
    clock = (
        f"{provider.ConsumeIntInRange(0, 24):02d}:{provider.ConsumeIntInRange(0, 60):02d}"
        f":{provider.ConsumeIntInRange(0, 60):02d}"
    )
    zone = provider.PickValueInList(
        ["", "Z", "+00:00", "-00:00"]
        + [f"{sign}{provider.ConsumeIntInRange(0, 24):02d}:{provider.ConsumeIntInRange(0, 59):02d}" for sign in "+-"],
    )
    return f"{date}T{clock}{zone}"


def check_created_since(provider: Any) -> None:
    choice = provider.ConsumeIntInRange(0, 2)
    if choice == 0:
        value = provider.PickValueInList(TIMESTAMPS)
    elif choice == 1:
        value = timestamp(provider)
    else:
        value = argument(provider)
    try:
        since = parse_created_since(value)
    except ToolError:
        return
    if since is None:
        invariant(value is None, f"created_since {value!r} was read as omitted")
        return
    invariant(
        since.tzinfo is UTC and since.utcoffset() == timedelta(0),
        f"parse_created_since gave {since!r}, not a time in UTC",
    )
    invariant(parse_created_since(since.isoformat()) == since, f"{since} does not read back as itself")


CHECKS = [
    check_integers, check_query_parameter, check_booleans, check_memory_ids, check_text_lists, check_created_since,
]


def test_one_input(data: bytes) -> None:
    provider = atheris.FuzzedDataProvider(data)
    provider.PickValueInList(CHECKS)(provider)


if __name__ == "__main__":
    run(test_one_input)
