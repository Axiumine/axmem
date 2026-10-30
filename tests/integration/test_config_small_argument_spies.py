# SPDX-FileCopyrightText: 2026 Giovanni Manzoni
# SPDX-License-Identifier: AGPL-3.0-only

"""Integration tests for four arguments that the small config, middleware and route modules hand on and that no result shows

The host of a request loses one trailing dot, the stderr handler is made for ``sys.stderr``, the server of a request context is
narrowed with ``typing.cast`` and the default format of ``configure_logging`` is checked by name. A wrong argument changes
nothing in the result for the inputs at hand, so each test hands the real function a value that records how it is used.
"""
import logging
import sys
from types import SimpleNamespace

import pytest

from app.config import logging_config
from app.config.settings import LOG_FORMATS
from app.middleware import loopback_guard
from app.middleware.loopback_guard import refusal_for
from app.routes import server_state


def test_the_host_of_a_request_loses_only_its_trailing_dot_when_it_is_compared_with_the_hosts_the_operator_named(monkeypatch):
    stripped = []

    class Name(str):
        """A host name that records the characters it is stripped of from the right."""

        __slots__ = ()

        def lower(self):
            return Name(super().lower())

        def strip(self, chars=None):
            return Name(super().strip(chars))

        def rstrip(self, chars=None):
            stripped.append(chars)
            return Name(super().rstrip(chars))

    monkeypatch.setattr(loopback_guard, "_hostname", lambda url: Name("Axmem.Example."))
    scope = {"type": "http", "headers": [(b"host", b"Axmem.Example.:8020")]}

    assert refusal_for(scope, allowed_hosts=frozenset({"axmem.example"})) is None
    assert stripped == [".", "."]  # once to ask whether the name is a loopback name, once to compare it with the named hosts


def test_the_stderr_handler_is_made_for_sys_stderr_and_is_handed_that_stream(monkeypatch):
    streams = []

    class Handler(logging.StreamHandler):
        def __init__(self, stream=None):
            streams.append(stream)
            super().__init__(stream)

    monkeypatch.setattr(logging_config.logging, "StreamHandler", Handler)

    handler = logging_config._stderr_handler("json")

    assert len(streams) == 1
    assert streams[0] is sys.stderr
    assert handler.stream is sys.stderr


def test_the_server_of_a_context_is_narrowed_to_the_axmem_server_by_a_cast_and_is_handed_back_as_it_is(monkeypatch):
    casts = []

    def cast(target, value):
        casts.append((target, value))
        return value

    monkeypatch.setattr(server_state, "cast", cast)
    mcp = SimpleNamespace(name="the server of the context")

    assert server_state.server_of(SimpleNamespace(fastmcp=mcp)) is mcp
    assert casts == [("AxmemMCP", mcp)]


def test_the_default_format_of_configure_logging_is_checked_as_console_and_not_changed_in_case(monkeypatch):
    class StopError(Exception):
        """Raised by the check, so that nothing of the logging is set up."""

    asked = []

    def canonical_choice(setting, value, choices):
        asked.append((setting, value, choices))
        raise StopError

    monkeypatch.setattr(logging_config, "canonical_choice", canonical_choice)

    with pytest.raises(StopError):
        logging_config.configure_logging()

    assert asked == [("LOG_FORMAT", "console", LOG_FORMATS)]
