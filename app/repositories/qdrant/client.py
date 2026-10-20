"""Connections to the Qdrant nodes: failover between nodes and retries of ambiguous failures.

Only this module and the store modules next to it import ``qdrant_client`` (see the import
contracts in pyproject.toml).
"""
import asyncio
import itertools
import json
import logging
import math
import warnings
from collections.abc import Awaitable, Callable
from typing import TypeVar, cast
from urllib.parse import urlsplit

import httpx
from qdrant_client import AsyncQdrantClient
from qdrant_client import models as qm
from qdrant_client.http.exceptions import ResponseHandlingException, UnexpectedResponse

from app.config.settings import settings
from app.exceptions import QdrantStartupError

logger = logging.getLogger(__name__)

T = TypeVar("T")

# Seconds waited before the first retry of an ambiguous failure; each later retry waits one more multiple.
RETRY_BACKOFF_SECONDS = 1.0

# The oldest server this service supports: submit-time resolution of conditional writes and the
# WAL replay fix arrived in 1.19.0, and update_mode in 1.17.0 (docs/configuration.md).
MINIMUM_SERVER_VERSION = (1, 19, 0)

_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})


def is_ambiguous(exc: BaseException) -> bool:
    """True when a failed request may or may not have been applied: a transport failure, or a 5xx answer."""
    if isinstance(exc, UnexpectedResponse):
        return exc.status_code is not None and exc.status_code >= 500
    return isinstance(exc, ResponseHandlingException | TimeoutError)


def describe_request_failure(exc: UnexpectedResponse | ResponseHandlingException) -> str:
    """One line that says why a Qdrant request failed and what to check, for an operator and not for a log.

    An answer of 401 or 403 means the API key is missing or wrong (Qdrant answers the version request without a
    key, so the key is first tried by the next request). A transport failure means the node cannot be reached.
    """
    if isinstance(exc, ResponseHandlingException):
        return (
            f"Qdrant cannot be reached or did not answer in time ({type(exc.source).__name__}); "
            "check QDRANT_URL and that the node runs"
        )
    if exc.status_code in (401, 403):
        return f"Qdrant refused the request (HTTP {exc.status_code}): check QDRANT_API_KEY"
    try:
        detail = " ".join(str(json.loads(exc.content)["status"]["error"]).split())[:200]
    except (ValueError, KeyError, TypeError):  # not JSON, or not the shape Qdrant answers with
        detail = exc.reason_phrase
    return f"Qdrant answered HTTP {exc.status_code}: {detail}"


def describe_failure(exc: BaseException) -> str | None:
    """``describe_request_failure`` for a Qdrant client error or a failed HTTP call to a node, else None."""
    if isinstance(exc, UnexpectedResponse | ResponseHandlingException):
        return describe_request_failure(exc)
    if isinstance(exc, httpx.HTTPStatusError):
        if exc.response.status_code in (401, 403):
            return f"Qdrant refused the request (HTTP {exc.response.status_code}): check QDRANT_API_KEY"
        return f"Qdrant answered HTTP {exc.response.status_code} to {exc.request.method} {exc.request.url.path}"
    if isinstance(exc, httpx.HTTPError):
        return (
            f"Qdrant cannot be reached or did not answer in time ({type(exc).__name__}); "
            "check QDRANT_URL and that the node runs"
        )
    return None


def parse_version(text: str) -> tuple[int, ...]:
    """The leading numeric components of a server version such as ``1.19.2``."""
    parts: list[int] = []
    for piece in text.split("."):
        digits = "".join(itertools.takewhile(str.isdigit, piece))
        if not digits:
            break
        parts.append(int(digits))
    return tuple(parts)


def _build_client(url: str, api_key: str | None, timeout: float) -> AsyncQdrantClient:
    """One client for one node. The key travels in a header, so a remote node should be reached over https."""
    host = urlsplit(url).hostname or ""
    with warnings.catch_warnings():
        # The client warns about a key on plain http for every client; say it once, and only for remote hosts
        warnings.simplefilter("ignore", UserWarning)
        client = AsyncQdrantClient(
            url=url,
            api_key=api_key,
            timeout=math.ceil(timeout),
            check_compatibility=False,  # the version is checked at start-up, where a failure stops the server
        )
    if api_key and url.startswith("http://") and host not in _LOOPBACK_HOSTS:
        logger.warning("The Qdrant API key is sent over plain http to a remote host; use https", extra={"host": host})
    return client


class NodePool:
    """The clients of the configured Qdrant nodes.

    Requests start at a different node each time, so a cluster is used evenly, and move on to the next node
    when one cannot be reached. An ambiguous failure of a write is retried with the same operation (the
    caller's claim token is part of it), never with a new one.
    """

    def __init__(self, urls: list[str] | None = None, api_key: str | None = None, timeout: float | None = None):
        self.urls = urls or settings.qdrant_urls
        key = api_key if api_key is not None else settings.QDRANT_API_KEY.get_secret_value()
        seconds = timeout if timeout is not None else settings.QDRANT_TIMEOUT_SECONDS
        self._api_key = key or None
        self._timeout = seconds
        self.clients = [_build_client(url, self._api_key, seconds) for url in self.urls]
        self._turn = itertools.count()

    def only(self, index: int) -> NodePool:
        """A view of this pool that talks to one node only (it shares that node's client, so do not close it)."""
        view = object.__new__(NodePool)
        view.urls = [self.urls[index]]
        view._api_key = self._api_key
        view._timeout = self._timeout
        view.clients = [self.clients[index]]
        view._turn = itertools.count()
        return view

    def _order(self) -> list[AsyncQdrantClient]:
        start = next(self._turn) % len(self.clients)
        return self.clients[start:] + self.clients[:start]

    async def run(self, call: Callable[[AsyncQdrantClient], Awaitable[T]], attempts: int = 1) -> T:
        """Run ``call`` on a node; after an ambiguous failure try again (on the next node) up to ``attempts`` times."""
        failure: BaseException = RuntimeError("no attempt was made")  # replaced by the first failure below
        order = itertools.cycle(self._order())
        for attempt in range(max(attempts, 1)):
            if attempt:
                await asyncio.sleep(RETRY_BACKOFF_SECONDS * attempt)
            try:
                return await call(next(order))
            except Exception as exc:
                if not is_ambiguous(exc):
                    raise
                failure = exc
                logger.warning(
                    "Qdrant request failed ambiguously",
                    extra={"attempt": attempt + 1, "attempts": attempts, "error_type": type(exc).__name__},
                )
        raise failure

    def http(self, url: str) -> httpx.AsyncClient:
        """A plain HTTP client for the Qdrant endpoints the Python client does not wrap (server info, snapshots)."""
        headers = {"api-key": self._api_key} if self._api_key else {}
        return httpx.AsyncClient(base_url=url, headers=headers, timeout=self._timeout)

    async def _version_of(self, url: str) -> str:
        """The version a node reports.

        Raises:
            QdrantStartupError: If the node cannot be reached or answers without a version
        """
        try:
            async with self.http(url) as http:
                response = await http.get("/")
                response.raise_for_status()
                return str(response.json()["version"])
        except (httpx.HTTPError, KeyError, ValueError) as exc:
            raise QdrantStartupError(
                f"Qdrant at {url} cannot be reached or did not report its version ({type(exc).__name__})",
            ) from exc

    async def server_versions(self, *, skip_silent: bool = False) -> list[tuple[str, str]]:
        """``(url, version)`` of the nodes.

        By default every node must answer. With ``skip_silent`` a node that does not answer is logged and left out
        while another one does.

        Raises:
            QdrantStartupError: If a node cannot be reached or answers without a version (with ``skip_silent``:
                only if no node does)
        """
        versions: list[tuple[str, str]] = []
        failures: list[QdrantStartupError] = []
        for url in self.urls:
            try:
                versions.append((url, await self._version_of(url)))
            except QdrantStartupError as exc:
                if not skip_silent:
                    raise
                failures.append(exc)
                logger.warning("A Qdrant node did not answer; going on with the others", extra={"node": url})
        if not versions:
            raise failures[0]
        return versions

    async def require_supported_server(self) -> None:
        """Refuse a node older than ``MINIMUM_SERVER_VERSION``: conditional writes are not safe on it.

        A node that does not answer is logged and skipped while another one does, so a cluster that lost a node
        can still start and be inspected (requests fail over to the nodes that answer, and ``qdrant-check``
        reports the silent one). Only when no node answers does it raise.

        Raises:
            QdrantStartupError: If no node can be reached, or a node that answers is too old
        """
        versions = await self.server_versions(skip_silent=True)
        for url, version in versions:
            if parse_version(version) < MINIMUM_SERVER_VERSION:
                minimum = ".".join(map(str, MINIMUM_SERVER_VERSION))
                raise QdrantStartupError(
                    f"Qdrant {version} at {url} is too old: version {minimum} or later is required",
                )

    async def create_snapshot(self, index: int, collection: str) -> str:
        """Take a snapshot of ``collection`` on node ``index`` (its local shards); return the snapshot's name."""
        description = cast(qm.SnapshotDescription, await self.clients[index].create_snapshot(collection, wait=True))
        return description.name

    async def delete_snapshot(self, index: int, collection: str, snapshot: str) -> None:
        await self.clients[index].delete_snapshot(collection, snapshot, wait=True)

    async def close(self) -> None:
        for client in self.clients:
            await client.close()

