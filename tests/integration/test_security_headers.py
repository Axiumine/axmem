# SPDX-FileCopyrightText: 2026 Giovanni Manzoni
# SPDX-License-Identifier: AGPL-3.0-only

"""Every answer of the HTTP server carries the security headers, the answers that refuse a request included.

The middleware is tested as ``main._http_middleware`` stacks it: in front of the loopback guard, CORS and the body limit,
over the real routes. A refusal produced further in (421, 403, 413) must still carry the headers, a header that the
application set itself must be left alone, a streamed body must not be held back, and ``Strict-Transport-Security`` is
for a server that serves TLS itself and for a host that is not loopback.
"""
import asyncio

import httpx
import pytest
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.requests import ClientDisconnect
from starlette.responses import JSONResponse
from starlette.routing import Route

import main
from app.config.settings import settings
from app.middleware.security_headers import SecurityHeadersMiddleware

LOCAL = "http://127.0.0.1:8020"
# Pinned here in full, so that a change of a value is a decision and not a side effect of a refactoring
SECURITY_HEADERS = {
    "x-content-type-options": "nosniff",
    "x-frame-options": "DENY",
    "referrer-policy": "no-referrer",
    "content-security-policy": "default-src 'none'; frame-ancestors 'none'",
    "cache-control": "no-store",
}
SAME_ORIGIN_ONLY = {"cross-origin-resource-policy": "same-origin"}
HSTS = "strict-transport-security"


def assert_carries(response, expected):
    for name, value in expected.items():
        assert response.headers.get_list(name) == [value], (name, dict(response.headers))


@pytest.fixture
def real_app():
    """The HTTP app of ``main`` with the middleware ``main._serve`` gives it, for the settings of the test."""
    return main.mcp.http_app(middleware=main._http_middleware())


def _client(app, base_url=LOCAL):
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=base_url)


# ---- the answers of the real routes -----------------------------------------------------------------------


@pytest.mark.parametrize("path", ["/", "/api/v1/auth/info"])
async def test_the_public_routes_answer_with_the_security_headers(real_app, path):
    async with _client(real_app) as client:
        response = await client.get(path)

    assert response.status_code == 200
    assert_carries(response, SECURITY_HEADERS | SAME_ORIGIN_ONLY)
    assert HSTS not in response.headers


async def test_an_unknown_route_and_a_wrong_method_carry_them_too(real_app):
    async with _client(real_app) as client:
        missing = await client.get("/no/such/route")
        wrong_method = await client.delete("/")

    assert (missing.status_code, wrong_method.status_code) == (404, 405)
    assert_carries(missing, SECURITY_HEADERS | SAME_ORIGIN_ONLY)
    assert_carries(wrong_method, SECURITY_HEADERS | SAME_ORIGIN_ONLY)


# ---- the answers that refuse a request, produced by the middleware behind it --------------------------------


@pytest.mark.parametrize(("headers", "status"), [
    ({"Host": "rebind.evil.example:8020"}, 421),
    ({"Origin": "https://evil.example"}, 403),
    ({"Origin": "null"}, 403),
])
async def test_the_refusals_of_the_loopback_guard_carry_them(real_app, headers, status):
    async with _client(real_app) as client:
        response = await client.get("/api/v1/memories", headers=headers)

    assert response.status_code == status
    assert_carries(response, SECURITY_HEADERS | SAME_ORIGIN_ONLY)


@pytest.mark.parametrize("auth_mode", ["none", "static"])
async def test_the_413_of_the_body_limit_carries_them(monkeypatch, auth_mode):
    monkeypatch.setattr(settings, "AUTH_MODE", auth_mode)
    monkeypatch.setattr(settings, "REQUEST_BODY_MAX_BYTES", 1024)
    app = main.mcp.http_app(middleware=main._http_middleware())

    async with _client(app) as client:
        response = await client.post("/api/v1/memories", content=b"x" * 2048)

    assert response.status_code == 413
    assert_carries(response, SECURITY_HEADERS | SAME_ORIGIN_ONLY)


async def test_the_413_of_a_chunked_body_carries_them(monkeypatch):
    """A body that declares no length is counted as the route reads it, and refused in the middle of the stream."""
    monkeypatch.setattr(settings, "REQUEST_BODY_MAX_BYTES", 1024)

    async def reads_the_body(request):
        try:
            return JSONResponse({"length": len(await request.body())})
        except ClientDisconnect:  # what the route sees once the middleware has answered 413; its own answer is dropped
            return JSONResponse({"error": "disconnected"})

    app = Starlette(routes=[Route("/upload", reads_the_body, methods=["POST"])], middleware=main._http_middleware())

    async def chunks():
        for _ in range(4):
            yield b"x" * 512

    async with _client(app) as client:
        response = await client.post("/upload", content=chunks())

    assert response.status_code == 413
    assert_carries(response, SECURITY_HEADERS | SAME_ORIGIN_ONLY)


# ---- the order of the stack and the settings it follows -------------------------------------------------------


def test_the_security_headers_are_outermost_and_follow_the_tls_and_cors_settings(monkeypatch):
    monkeypatch.setattr(settings, "AUTH_MODE", "none")
    monkeypatch.setattr(settings, "CORS_ENABLED", False)
    monkeypatch.setattr(settings, "SERVER_SSL_CERTFILE", "")
    plain = main._http_middleware()
    monkeypatch.setattr(settings, "CORS_ENABLED", True)
    monkeypatch.setattr(settings, "SERVER_SSL_CERTFILE", "/etc/axmem/server.pem")
    served_tls = main._http_middleware()

    assert [entry.cls.__name__ for entry in plain] == ["SecurityHeadersMiddleware", "LoopbackGuardMiddleware", "BodyLimitMiddleware"]
    assert plain[0].cls is SecurityHeadersMiddleware
    assert plain[0].kwargs == {"tls": False, "cors": False}
    assert served_tls[0].cls is SecurityHeadersMiddleware
    assert served_tls[0].kwargs == {"tls": True, "cors": True}


async def test_a_server_with_cors_does_not_send_the_cross_origin_resource_policy(monkeypatch):
    monkeypatch.setattr(settings, "CORS_ENABLED", True)
    monkeypatch.setattr(settings, "CORS_ORIGINS", ["https://app.example"])
    app = main.mcp.http_app(middleware=main._http_middleware())

    async with _client(app) as client:
        response = await client.get("/", headers={"Origin": "https://app.example"})

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "https://app.example"
    assert_carries(response, SECURITY_HEADERS)
    assert "cross-origin-resource-policy" not in response.headers


async def test_an_unhandled_exception_is_answered_outside_every_middleware_without_the_headers():
    """Starlette's outermost layer answers a ``500``; the documented limit of the middleware (docs/configuration.md)."""

    async def broken(request):
        raise RuntimeError("a bug")

    app = Starlette(routes=[Route("/broken", broken)], middleware=main._http_middleware())

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app, raise_app_exceptions=False), base_url=LOCAL) as client:
        response = await client.get("/broken")

    assert response.status_code == 500
    assert "x-content-type-options" not in response.headers


# ---- a header the application set stays; the other messages are not touched -----------------------------------


def _own_headers_app():
    async def cached(request):
        return JSONResponse(
            {"ok": True}, headers={"Cache-Control": "private, max-age=60", "x-frame-options": "SAMEORIGIN"},
        )

    return Starlette(routes=[Route("/cached", cached)], middleware=main._http_middleware())


async def test_a_header_that_the_route_set_is_not_overwritten_or_repeated():
    async with _client(_own_headers_app()) as client:
        response = await client.get("/cached")

    assert response.headers.get_list("cache-control") == ["private, max-age=60"]
    assert response.headers.get_list("x-frame-options") == ["SAMEORIGIN"]
    assert_carries(response, {k: v for k, v in SECURITY_HEADERS.items() if k not in ("cache-control", "x-frame-options")})


async def _drive(middleware, scope):
    """Run one request through the middleware by hand; the messages it sent on, in order."""
    sent = []

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        sent.append(message)

    await middleware(scope, receive, send)
    return sent


async def test_a_streamed_body_is_passed_on_as_it_is_produced_and_keeps_its_own_cache_control():
    """The MCP event stream sets ``Cache-Control: no-cache, no-transform`` and must not be held until it ends."""
    release = asyncio.Event()
    first_chunk_seen = asyncio.Event()
    sent = []

    async def app(scope, receive, send):
        await send({
            "type": "http.response.start", "status": 200,
            "headers": [(b"content-type", b"text/event-stream"), (b"cache-control", b"no-cache, no-transform")],
        })
        await send({"type": "http.response.body", "body": b"data: one\n\n", "more_body": True})
        await release.wait()
        await send({"type": "http.response.body", "body": b"data: two\n\n", "more_body": False})

    async def send(message):
        sent.append(message)
        if message["type"] == "http.response.body":
            first_chunk_seen.set()

    async def receive():  # pragma: no cover - the application never reads the request
        return {"type": "http.disconnect"}

    request = asyncio.create_task(
        SecurityHeadersMiddleware(app)({"type": "http", "headers": [(b"host", b"127.0.0.1")]}, receive, send),
    )
    await asyncio.wait_for(first_chunk_seen.wait(), timeout=5)

    assert not request.done()  # the application is still streaming, and its first event is already out
    start = dict(sent[0]["headers"])
    assert start[b"cache-control"] == b"no-cache, no-transform"
    assert start[b"x-content-type-options"] == b"nosniff"
    release.set()
    await asyncio.wait_for(request, timeout=5)
    assert [message.get("body") for message in sent[1:]] == [b"data: one\n\n", b"data: two\n\n"]


async def test_a_response_start_without_a_headers_entry_gets_them():
    async def app(scope, receive, send):
        await send({"type": "http.response.start", "status": 204})
        await send({"type": "http.response.body"})

    sent = await _drive(SecurityHeadersMiddleware(app), {"type": "http", "headers": []})

    assert dict(sent[0]["headers"]) == {  # the defaults of the middleware: no HSTS, and the resource policy
        b"x-content-type-options": b"nosniff",
        b"x-frame-options": b"DENY",
        b"referrer-policy": b"no-referrer",
        b"content-security-policy": b"default-src 'none'; frame-ancestors 'none'",
        b"cache-control": b"no-store",
        b"cross-origin-resource-policy": b"same-origin",
    }
    assert sent[1] == {"type": "http.response.body"}


@pytest.mark.parametrize("scope_type", ["lifespan", "websocket"])
async def test_a_scope_that_is_not_http_goes_to_the_application_untouched(scope_type):
    seen = []

    async def app(scope, receive, send):
        seen.append((scope, receive, send))

    async def receive():  # pragma: no cover - never called
        return {}

    async def send(message):  # pragma: no cover - never called
        return None

    scope = {"type": scope_type}
    await SecurityHeadersMiddleware(app, tls=True)(scope, receive, send)

    assert seen == [(scope, receive, send)]


# ---- Strict-Transport-Security --------------------------------------------------------------------------


def _hsts_app(tls):
    async def ping(request):
        return JSONResponse({"ok": True})

    return Starlette(routes=[Route("/", ping)], middleware=[Middleware(SecurityHeadersMiddleware, tls=tls)])


@pytest.mark.parametrize("host", ["axmem.example", "axmem.example:8443", "203.0.113.7:8020", "[2001:db8::1]:8020"])
async def test_a_server_that_serves_tls_sends_hsts_to_a_host_that_is_not_loopback(host):
    async with _client(_hsts_app(tls=True), base_url=f"https://{host}") as client:
        response = await client.get("/")

    assert response.headers.get_list(HSTS) == ["max-age=31536000"]  # no includeSubDomains, no preload


@pytest.mark.parametrize("host", ["localhost:8020", "LOCALHOST", "localhost.:8020", "127.0.0.1:8020", "127.1.2.3", "[::1]:8020"])
async def test_hsts_is_withheld_from_a_loopback_host_because_a_browser_pins_the_name_for_every_port(host):
    async with _client(_hsts_app(tls=True), base_url=f"https://{host}") as client:
        response = await client.get("/")

    assert HSTS not in response.headers


@pytest.mark.parametrize("host", ["axmem.example", "127.0.0.1:8020"])
async def test_a_server_that_does_not_serve_tls_never_sends_hsts(host):
    async with _client(_hsts_app(tls=False), base_url=f"http://{host}") as client:
        response = await client.get("/")

    assert HSTS not in response.headers


async def test_hsts_is_sent_when_the_request_has_no_host_or_more_than_one():
    async def app(scope, receive, send):
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body"})

    without_host = await _drive(SecurityHeadersMiddleware(app, tls=True), {"type": "http", "headers": []})
    two_hosts = await _drive(
        SecurityHeadersMiddleware(app, tls=True),
        {"type": "http", "headers": [(b"host", b"localhost"), (b"host", b"localhost")]},
    )

    assert dict(without_host[0]["headers"])[b"strict-transport-security"] == b"max-age=31536000"
    assert dict(two_hosts[0]["headers"])[b"strict-transport-security"] == b"max-age=31536000"
