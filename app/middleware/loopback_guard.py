"""Refuses requests that a web page could send to a server that has no credential.

``AUTH_MODE=none`` is accepted on a loopback bind only, and the bind address is then the whole of its
protection. A browser on the same machine can still reach that address, so two kinds of page can use it:

- a page on any site that sends a cross-origin request (a ``fetch`` with ``mode: 'no-cors'`` needs no
  preflight) and plants or deletes memories of the default tenant;
- a page that uses DNS rebinding: its host name is made to resolve to 127.0.0.1 after the page has loaded, so
  the browser treats the requests as same-origin and lets the page read the answers.

Both are stopped by looking at the request. A browser always sends the real ``Host`` of the URL it fetches,
and sends ``Origin`` on every cross-origin request and every ``POST``. A request whose ``Host`` is not a
loopback name (``localhost`` or a loopback address) or one the operator named in ``AXMEM_ALLOWED_HOSTS`` is
answered 421, and a request that carries an ``Origin`` that is neither a loopback origin nor one the operator named
in ``CORS_ORIGINS`` (which counts only with ``CORS_ENABLED``) is answered 403, for the REST routes and for the MCP
endpoint alike (the check runs before routing, so no route can forget it). Clients that are not browsers send
neither a foreign ``Host`` nor an ``Origin`` and are not affected. The MCP SDK has a check of the same kind, but FastMCP does not
pass it to its HTTP transport, so this middleware does the work for the whole application.

The middleware is added only to the HTTP app of ``AUTH_MODE=none`` (``main._http_middleware``); stdio has no
HTTP and the credential modes do not need it. Only HTTP requests are checked: the application serves no
WebSocket route.
"""
from collections.abc import Collection
from urllib.parse import urlsplit

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from app.config.auth import is_loopback_host

HOST_REFUSED = 421
ORIGIN_REFUSED = 403


def is_loopback_name(hostname: str | None) -> bool:
    """Whether a host name from a ``Host`` or ``Origin`` header is ``localhost`` or a loopback address."""
    if not hostname:
        return False
    return hostname.lower().rstrip(".") == "localhost" or is_loopback_host(hostname)


def _hostname(url: str) -> str | None:
    """The host name of a URL or of a ``//host[:port]`` value, without brackets or port; None when unreadable."""
    try:
        return urlsplit(url).hostname
    except ValueError:
        return None


def _header_values(scope: Scope, name: bytes) -> list[str]:
    return [value.decode("latin-1") for key, value in scope["headers"] if key == name]


def normalise_hosts(hosts: Collection[str]) -> frozenset[str]:
    """The host names an operator listed, trimmed and lower-cased (a blank entry names nothing)."""
    return frozenset(host.strip().lower().rstrip(".") for host in hosts if host.strip())


def _host_is_acceptable(host_header: str, allowed_hosts: Collection[str]) -> bool:
    name = _hostname(f"//{host_header}")
    return is_loopback_name(name) or (name is not None and name.lower().rstrip(".") in allowed_hosts)


def refusal_for(
    scope: Scope, allowed_origins: Collection[str] = (), allowed_hosts: Collection[str] = (),
) -> JSONResponse | None:
    """The response that refuses the request, or None when its ``Host`` is acceptable and so is its ``Origin``.

    A ``Host`` is acceptable when it names a loopback host or one of ``allowed_hosts`` (already normalised by
    ``normalise_hosts``). An ``Origin`` that is listed in ``allowed_origins`` (compared exactly, as CORS does)
    passes as well.
    """
    hosts = _header_values(scope, b"host")
    if len(hosts) != 1 or not _host_is_acceptable(hosts[0], allowed_hosts):
        return JSONResponse(
            {"error": "Invalid Host header: this server answers loopback host names only"},
            status_code=HOST_REFUSED,
        )

    for origin in _header_values(scope, b"origin"):
        # "null", sent by sandboxed pages, has no host and is never allowed
        if origin not in allowed_origins and not is_loopback_name(_hostname(origin)):
            return JSONResponse(
                {"error": "Invalid Origin header: this server accepts loopback origins only"},
                status_code=ORIGIN_REFUSED,
            )
    return None


class LoopbackGuardMiddleware:
    """ASGI middleware: HTTP requests with a foreign ``Host`` or ``Origin`` never reach the application."""

    def __init__(self, app: ASGIApp, allowed_origins: Collection[str] = (), allowed_hosts: Collection[str] = ()):
        self.app = app
        self.allowed_origins = frozenset(allowed_origins)
        self.allowed_hosts = normalise_hosts(allowed_hosts)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http":
            refusal = refusal_for(scope, self.allowed_origins, self.allowed_hosts)
            if refusal is not None:
                await refusal(scope, receive, send)
                return
        await self.app(scope, receive, send)
