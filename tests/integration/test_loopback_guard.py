"""Integration tests for the loopback guard of the credential-free HTTP server

With ``AUTH_MODE=none`` the bind address is the only protection, and a web page can still reach it by DNS
rebinding (a foreign ``Host``) or by a cross-origin request (a foreign ``Origin``). The guard is driven here as
the ASGI application it is, with raw scopes so that missing and repeated headers can be sent too.
"""
import json

import pytest

from app.middleware.loopback_guard import (
    HOST_REFUSED,
    ORIGIN_REFUSED,
    LoopbackGuardMiddleware,
    is_loopback_name,
    refusal_for,
)


class Reached(Exception):
    """The request got past the guard."""


async def _application(scope, receive, send):
    await send({"type": "http.response.start", "status": 200, "headers": []})
    await send({"type": "http.response.body", "body": b"reached"})


async def _call(headers: list[tuple[bytes, bytes]], scope_type: str = "http", allowed_origins=(), allowed_hosts=()):
    """Send one request through the guard and return (status, body), status 200 meaning it got through."""
    guard = LoopbackGuardMiddleware(_application, allowed_origins=allowed_origins, allowed_hosts=allowed_hosts)
    sent = []

    async def receive():
        return {"type": "http.request", "body": b""}

    async def send(message):
        sent.append(message)

    await guard({"type": scope_type, "headers": headers}, receive, send)
    status = next(message["status"] for message in sent if message["type"] == "http.response.start")
    body = b"".join(message.get("body", b"") for message in sent if message["type"] == "http.response.body")
    return status, body


def _headers(host: str | None = "127.0.0.1:8020", *origins: str) -> list[tuple[bytes, bytes]]:
    headers = [] if host is None else [(b"host", host.encode())]
    return headers + [(b"origin", origin.encode()) for origin in origins]


@pytest.mark.parametrize("host", [
    "127.0.0.1", "127.0.0.1:8020", "127.1.2.3:80", "localhost", "localhost:8020", "LOCALHOST:8020", "localhost.:8020",
    "[::1]", "[::1]:8020",
])
async def test_a_loopback_host_reaches_the_application(host):
    status, body = await _call(_headers(host))

    assert (status, body) == (200, b"reached")


@pytest.mark.parametrize("host", [
    "evil.example", "evil.example:8020", "rebind.evil.example", "localhost.evil.example", "127.0.0.1.evil.example",
    "192.168.1.10:8020", "0.0.0.0:8020", "[2001:db8::1]:8020", "", "[",
])
async def test_a_host_that_is_not_loopback_is_refused_with_421(host):
    status, body = await _call(_headers(host))

    assert status == HOST_REFUSED == 421
    assert "Host" in json.loads(body)["error"]


async def test_a_request_without_a_host_is_refused():
    status, _ = await _call(_headers(None))

    assert status == 421


async def test_a_request_with_two_hosts_is_refused():
    status, _ = await _call([(b"host", b"127.0.0.1:8020"), (b"host", b"evil.example")])

    assert status == 421


@pytest.mark.parametrize("origin", [
    "http://127.0.0.1:8020", "http://localhost:3000", "https://localhost", "http://[::1]:8020", "http://LOCALHOST:5173",
])
async def test_a_loopback_origin_reaches_the_application(origin):
    status, _ = await _call(_headers("127.0.0.1:8020", origin))

    assert status == 200


@pytest.mark.parametrize("origin", [
    "https://evil.example", "http://evil.example:8020", "http://localhost.evil.example", "null", "", "not a url",
    "http://[", "file://", "http://192.168.1.10:8020",
])
async def test_a_foreign_origin_is_refused_with_403(origin):
    status, body = await _call(_headers("127.0.0.1:8020", origin))

    assert status == ORIGIN_REFUSED == 403
    assert "Origin" in json.loads(body)["error"]


async def test_every_origin_header_has_to_be_acceptable():
    status, _ = await _call(_headers("127.0.0.1:8020", "http://localhost:3000", "https://evil.example"))

    assert status == 403


async def test_a_rebound_host_is_refused_before_its_origin_is_looked_at():
    status, _ = await _call(_headers("rebind.evil.example", "http://rebind.evil.example"))

    assert status == 421


async def test_an_origin_the_operator_named_passes_and_others_do_not():
    named = ("https://app.example.test",)

    assert (await _call(_headers("127.0.0.1:8020", "https://app.example.test"), allowed_origins=named))[0] == 200
    assert (await _call(_headers("127.0.0.1:8020", "https://app.example.test:8443"), allowed_origins=named))[0] == 403
    assert (await _call(_headers("127.0.0.1:8020", "https://evil.example"), allowed_origins=named))[0] == 403


async def test_a_named_origin_does_not_excuse_a_foreign_host():
    status, _ = await _call(_headers("evil.example", "https://app.example.test"), allowed_origins=("https://app.example.test",))

    assert status == 421


async def test_a_wildcard_is_not_a_named_origin():
    status, _ = await _call(_headers("127.0.0.1:8020", "https://evil.example"), allowed_origins=("*",))

    assert status == 403


@pytest.mark.parametrize("host", ["host.docker.internal", "host.docker.internal:8020", "HOST.Docker.Internal.:8020"])
async def test_a_host_the_operator_named_reaches_the_application(host):
    status, _ = await _call(_headers(host), allowed_hosts=("host.docker.internal",))

    assert status == 200


@pytest.mark.parametrize("host", [
    "evil.example", "host.docker.internal.evil.example", "evil.host.docker.internal", "docker.internal", "",
])
async def test_other_hosts_stay_refused_when_one_is_named(host):
    status, _ = await _call(_headers(host), allowed_hosts=("host.docker.internal",))

    assert status == 421


@pytest.mark.parametrize("named", [("*",), ("",), ("  ",), ()])
async def test_a_blank_or_wildcard_host_entry_names_no_host(named):
    status, _ = await _call(_headers("evil.example"), allowed_hosts=named)

    assert status == 421


async def test_the_entries_are_trimmed_and_compared_without_regard_to_case():
    status, _ = await _call(_headers("docker-host.example:8020"), allowed_hosts=("  Docker-Host.Example ",))

    assert status == 200


async def test_a_named_host_does_not_excuse_a_foreign_origin():
    status, _ = await _call(
        _headers("host.docker.internal", "https://evil.example"), allowed_hosts=("host.docker.internal",),
    )

    assert status == 403


async def test_a_named_host_does_not_excuse_a_second_host_header():
    status, _ = await _call(
        [(b"host", b"host.docker.internal"), (b"host", b"evil.example")], allowed_hosts=("host.docker.internal",),
    )

    assert status == 421


@pytest.mark.parametrize("scope_type", ["lifespan", "websocket"])
async def test_only_http_requests_are_checked(scope_type):
    reached = []

    async def application(scope, receive, send):
        reached.append(scope["type"])

    guard = LoopbackGuardMiddleware(application)

    async def unused(*_):
        raise AssertionError("the guard answered a scope that is not HTTP")

    await guard({"type": scope_type, "headers": _headers("evil.example", "https://evil.example")}, unused, unused)

    assert reached == [scope_type]


def test_refusal_for_returns_nothing_for_a_request_that_may_pass():
    assert refusal_for({"headers": _headers("localhost:8020", "http://localhost:3000")}) is None


@pytest.mark.parametrize(("name", "expected"), [
    ("localhost", True), ("LocalHost", True), ("localhost.", True), ("127.0.0.1", True), ("::1", True),
    ("evil.example", False), ("", False), (None, False), ("127.0.0.1.evil.example", False),
])
def test_is_loopback_name(name, expected):
    assert is_loopback_name(name) is expected
