# SPDX-FileCopyrightText: 2026 Giovanni Manzoni
# SPDX-License-Identifier: AGPL-3.0-only

"""Adds the HTTP security headers to every response of the HTTP transport, the refusals of the other middleware included.

Every answer of the REST routes, ``/mcp`` and the discovery routes is JSON or an event stream; none is a page. The
headers say so to a browser that fetches one of them anyway, and keep it from reading, framing, caching or
forwarding the address of what it finds there:

- ``X-Content-Type-Options: nosniff``: a body is never taken for another media type than the one declared.
- ``X-Frame-Options: DENY`` and ``Content-Security-Policy: default-src 'none'; frame-ancestors 'none'``: nothing is
  framed, and a body that a browser did render could load and run nothing.
- ``Referrer-Policy: no-referrer``: a link followed from an answer leaks no address.
- ``Cache-Control: no-store``: an answer carries memories and is kept by no cache.
- ``Cross-Origin-Resource-Policy: same-origin``: a page of another origin cannot embed an answer with a ``no-cors``
  request. A browser applies it to ``no-cors`` requests only, so a client that calls with CORS, and every program that
  is not a browser, is not affected; with ``CORS_ENABLED`` the operator has invited other origins in, so it is left out.
- ``Strict-Transport-Security: max-age=31536000``, only when this process serves TLS itself (``SERVER_SSL_CERTFILE``):
  behind a TLS-terminating proxy the proxy knows whether the client came in over TLS and sends it. It is withheld from an
  answer to a loopback ``Host`` (``localhost`` or a loopback address): a browser pins a host name whatever the port, so
  the header would force ``https`` onto every other server the developer runs on ``localhost``. Neither
  ``includeSubDomains`` nor ``preload`` is sent: they commit a whole domain, which is the operator's decision.

A header that the application already set is left as it is (the MCP event stream sets its own ``Cache-Control``), so
the middleware only fills gaps. It is a plain ASGI middleware that touches the response start and passes every other
message through untouched, so a streamed body is not buffered. It sits outermost among the middleware of
``main._http_middleware``: the ``421``/``403`` of the loopback guard, the ``413`` of the body limit and the ``401``/
``403`` of the routes and of the MCP authentication pass through it. An unhandled exception is answered by Starlette's
own outermost layer, outside every middleware, and carries none of these headers. Only HTTP requests are touched; the
application serves no WebSocket route.
"""
from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.middleware.loopback_guard import names_loopback_host

# Lower-case names, as Starlette stores them. The values are fixed; see the module docstring for what each is for.
SECURITY_HEADERS: tuple[tuple[str, str], ...] = (
    ("x-content-type-options", "nosniff"),
    ("x-frame-options", "DENY"),
    ("referrer-policy", "no-referrer"),
    ("content-security-policy", "default-src 'none'; frame-ancestors 'none'"),
    ("cache-control", "no-store"),
)
CROSS_ORIGIN_RESOURCE_POLICY = ("cross-origin-resource-policy", "same-origin")
STRICT_TRANSPORT_SECURITY = ("strict-transport-security", "max-age=31536000")


class SecurityHeadersMiddleware:
    """ASGI middleware that adds the security headers to the response of every HTTP request, unless it has them."""

    def __init__(self, app: ASGIApp, tls: bool = False, cors: bool = False) -> None:
        self.app = app
        self.tls = tls
        self.headers = SECURITY_HEADERS if cors else (*SECURITY_HEADERS, CROSS_ORIGIN_RESOURCE_POLICY)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = self.headers
        if self.tls and not names_loopback_host(scope):
            headers = (*headers, STRICT_TRANSPORT_SECURITY)

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                message.setdefault("headers", [])
                response_headers = MutableHeaders(scope=message)
                for name, value in headers:
                    response_headers.setdefault(name, value)
            await send(message)

        await self.app(scope, receive, send_with_headers)
