# SPDX-FileCopyrightText: 2026 Giovanni Manzoni
# SPDX-License-Identifier: AGPL-3.0-only

"""Integration tests for the answers of the loopback guard and what it hands on (app/middleware/loopback_guard.py)

The wording of a refusal is what a developer meets in the browser console when a page is turned away, and the
application behind the guard has to receive the request exactly as it was sent.
"""
import json

import pytest

from app.middleware import loopback_guard
from app.middleware.loopback_guard import (
    HOST_REFUSED,
    ORIGIN_REFUSED,
    LoopbackGuardMiddleware,
    normalise_hosts,
    refusal_for,
)

HOST_ERROR = "Invalid Host header: this server answers loopback host names only"
ORIGIN_ERROR = "Invalid Origin header: this server accepts loopback origins only"


def _scope(host: str | None = "127.0.0.1:8020", *origins: str, kind: str = "http") -> dict:
    headers = [] if host is None else [(b"host", host.encode())]
    return {"type": kind, "headers": headers + [(b"origin", origin.encode()) for origin in origins]}


def _body(response) -> dict:
    return json.loads(response.body)


def test_a_foreign_host_is_answered_421_with_the_reason():
    refusal = refusal_for(_scope("rebind.evil.example:8020"))

    assert (refusal.status_code, _body(refusal)) == (HOST_REFUSED, {"error": HOST_ERROR})
    assert HOST_REFUSED == 421


def test_a_foreign_origin_is_answered_403_with_the_reason():
    refusal = refusal_for(_scope("127.0.0.1:8020", "https://evil.example"))

    assert (refusal.status_code, _body(refusal)) == (ORIGIN_REFUSED, {"error": ORIGIN_ERROR})
    assert ORIGIN_REFUSED == 403


@pytest.mark.parametrize("origin", ["null", "", "not a url", "http://[", "file:///tmp/page.html"])
def test_an_origin_without_a_loopback_host_is_refused(origin):
    refusal = refusal_for(_scope("127.0.0.1:8020", origin))

    assert (refusal.status_code, _body(refusal)) == (403, {"error": ORIGIN_ERROR})


def test_the_host_is_judged_before_the_origin():
    refusal = refusal_for(_scope("evil.example", "https://evil.example"))

    assert refusal.status_code == 421


def test_a_request_with_a_loopback_host_and_origin_is_not_refused():
    assert refusal_for(_scope("localhost:8020", "http://localhost:3000", "http://127.0.0.1:5173")) is None


def test_the_hosts_an_operator_named_are_trimmed_lower_cased_and_stripped_of_a_trailing_dot():
    assert normalise_hosts(["Axmem.Example.", "  other.example  ", "THIRD.example.", "", "   "]) == frozenset(
        {"axmem.example", "other.example", "third.example"},
    )
    assert normalise_hosts([]) == frozenset()


@pytest.mark.parametrize("host", ["axmem.example", "axmem.example:8080", "AXMEM.EXAMPLE", "axmem.example.", "Axmem.Example.:443"])
def test_a_host_the_operator_named_with_a_trailing_dot_is_accepted_in_every_spelling(host):
    guard = LoopbackGuardMiddleware(None, allowed_hosts=["Axmem.Example."])

    assert refusal_for(_scope(host), allowed_hosts=guard.allowed_hosts) is None


@pytest.mark.parametrize("host", ["axmem.example.evil", "xaxmem.example", "other.example"])
def test_a_host_that_only_resembles_a_named_one_is_refused(host):
    refusal = refusal_for(_scope(host), allowed_hosts=normalise_hosts(["axmem.example"]))

    assert refusal.status_code == 421


def test_an_origin_the_operator_named_is_compared_exactly():
    allowed = ["https://app.example.test"]

    assert refusal_for(_scope("127.0.0.1", "https://app.example.test"), allowed_origins=allowed) is None
    assert refusal_for(_scope("127.0.0.1", "https://app.example.test:8443"), allowed_origins=allowed).status_code == 403
    assert refusal_for(_scope("127.0.0.1", "HTTPS://APP.EXAMPLE.TEST"), allowed_origins=allowed).status_code == 403


async def test_the_application_behind_the_guard_receives_the_scope_receive_and_send_it_was_given():
    seen = []

    async def application(scope, receive, send):
        seen.append((scope, receive, send))

    async def receive():
        return {"type": "http.request"}

    async def send(message):
        pass

    for kind in ("http", "lifespan", "websocket"):
        scope = _scope(kind=kind)
        await LoopbackGuardMiddleware(application)(scope, receive, send)
        assert seen[-1][0] is scope
        assert seen[-1][1] is receive
        assert seen[-1][2] is send

    assert len(seen) == 3


async def test_a_scope_that_is_not_http_is_not_judged():
    reached = []

    async def application(scope, receive, send):
        reached.append(scope["type"])

    for kind in ("lifespan", "websocket"):
        await LoopbackGuardMiddleware(application)(_scope("evil.example", "https://evil.example", kind=kind), None, None)

    assert reached == ["lifespan", "websocket"]


async def test_a_refused_request_never_reaches_the_application_and_is_answered_through_the_given_send():
    sent = []
    reached = []

    async def application(scope, receive, send):
        reached.append(scope)

    async def send(message):
        sent.append(message)

    await LoopbackGuardMiddleware(application)(_scope("evil.example"), None, send)

    assert reached == []
    assert [message["type"] for message in sent] == ["http.response.start", "http.response.body"]
    assert sent[0]["status"] == 421
    assert json.loads(sent[1]["body"]) == {"error": HOST_ERROR}


async def test_the_refusal_is_called_with_the_scope_and_the_channels_of_the_request(monkeypatch):
    handed = []
    real = loopback_guard.refusal_for

    def spying(scope, allowed_origins, allowed_hosts):
        response = real(scope, allowed_origins, allowed_hosts)

        async def respond(*channels):
            handed.append(channels)
            await response(*channels)

        return respond

    monkeypatch.setattr(loopback_guard, "refusal_for", spying)

    async def application(scope, receive, send):
        raise AssertionError("a refused request reached the application")

    async def receive():
        return {"type": "http.request", "body": b""}

    async def send(message):
        pass

    scope = _scope("evil.example")
    await LoopbackGuardMiddleware(application)(scope, receive, send)

    assert len(handed) == 1
    assert [channel is expected for channel, expected in zip(handed[0], (scope, receive, send), strict=True)] == [True] * 3
