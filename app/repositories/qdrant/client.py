"""Connections to the Qdrant nodes: failover between nodes and retries of ambiguous failures.

Only this module and the store modules next to it import ``qdrant_client`` (see the import
contracts in pyproject.toml).
"""
import asyncio
import itertools
import json
import logging
import math
import time
import warnings
from collections.abc import Awaitable, Callable
from typing import TypeVar
from urllib.parse import urlsplit

import httpx
from qdrant_client import AsyncQdrantClient
from qdrant_client.http.exceptions import ResponseHandlingException, UnexpectedResponse

from app.config.settings import settings
from app.exceptions import QdrantStartupError

logger = logging.getLogger(__name__)

T = TypeVar("T")

# Seconds waited before the first retry of an ambiguous failure; each later retry waits one more multiple.
RETRY_BACKOFF_SECONDS = 1.0

# Seconds a node may take to build a snapshot of a collection. The call waits for every local shard to be written out,
# so the timeout of an ordinary request would give up on a large collection while the node goes on and keeps the snapshot.
SNAPSHOT_TIMEOUT_SECONDS = 3600.0

# After a snapshot request that gave up without an answer, how long the pool keeps looking for the snapshot the node may
# still be finishing (to delete it), and how often it looks. The node goes on after the client has given up, and a
# snapshot is only listed once it is complete.
SNAPSHOT_CLEANUP_SECONDS = 300.0
SNAPSHOT_CLEANUP_POLL_SECONDS = 2.0

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
    """``describe_request_failure`` for an error of the Qdrant client, else None.

    A plain ``httpx`` error is not Qdrant's alone (the snapshot download is, but the gateway, the rerank service and the
    introspection endpoint leave the same errors): ``app.repositories.service_failures`` names the server by the
    address of the request.
    """
    if isinstance(exc, UnexpectedResponse | ResponseHandlingException):
        return describe_request_failure(exc)
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
        failure: BaseException  # set by the first ambiguous failure below: an attempt that does not fail ends the call
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

    def http(self, url: str, timeout: float | None = None) -> httpx.AsyncClient:
        """A plain HTTP client for the Qdrant endpoints the Python client does not wrap (server info, snapshots)."""
        headers = {"api-key": self._api_key} if self._api_key else {}
        return httpx.AsyncClient(base_url=url, headers=headers, timeout=self._timeout if timeout is None else timeout)

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
        """Take a snapshot of ``collection`` on node ``index`` (its local shards); return the snapshot's name.

        The request waits for the node to finish, under its own long timeout (``SNAPSHOT_TIMEOUT_SECONDS``): the Python
        client takes no timeout per call, and a request that gave up early would leave the snapshot on the node unnamed.
        A request can still fail after the node has made, or while it is still making, the snapshot (a timeout beyond
        the bound, a connection that dropped, an answer without a name). The snapshots of the collection are listed
        before the request, and after a failure the ones that were not there are deleted (``_remove_new_snapshots``),
        so a failed backup leaves nothing on the node. This assumes nobody else takes a snapshot of the same
        collection on the same node at the same time.
        """
        before = await self._snapshot_names_or_none(index, collection)
        timeout = max(self._timeout, SNAPSHOT_TIMEOUT_SECONDS)
        try:
            async with self.http(self.urls[index], timeout=timeout) as http:
                response = await http.post(f"/collections/{collection}/snapshots", params={"wait": "true"})
                response.raise_for_status()
                return str(response.json()["result"]["name"])
        except BaseException as exc:
            # A transport failure leaves the node working: it may finish the snapshot after we looked, so look for a while
            await self._remove_new_snapshots(index, collection, before, patient=isinstance(exc, httpx.TransportError))
            raise

    async def _snapshot_names(self, index: int, collection: str) -> set[str]:
        return {snapshot.name for snapshot in await self.clients[index].list_snapshots(collection)}

    async def _snapshot_names_or_none(self, index: int, collection: str) -> set[str] | None:
        """The snapshots ``collection`` has on node ``index``, or None when the node will not list them (the request that follows says why)."""
        try:
            return await self._snapshot_names(index, collection)
        except Exception:  # noqa: BLE001 - a node that cannot list its snapshots fails the request that follows with its own error
            logger.warning(
                "The snapshots of a collection could not be listed before taking one, so a failed snapshot cannot be cleaned up",
                extra={"node": self.urls[index], "collection": collection},
            )
            return None

    async def _remove_new_snapshots(
        self, index: int, collection: str, before: set[str] | None, *, patient: bool,
    ) -> None:
        """Delete the snapshots of ``collection`` on node ``index`` that were not there ``before`` the request failed.

        ``patient`` keeps looking for ``SNAPSHOT_CLEANUP_SECONDS`` when none has appeared, because the node may still be
        making one. Nothing here raises: the failure that brought us here is the one that surfaces.
        """
        if before is None:
            return
        node = self.urls[index]
        deadline = time.monotonic() + (SNAPSHOT_CLEANUP_SECONDS if patient else 0.0)
        while True:
            try:
                new = sorted(await self._snapshot_names(index, collection) - before)
            except Exception:  # noqa: BLE001 - the original failure is what the caller must see
                logger.warning(
                    "The snapshots of a collection could not be listed after a failed snapshot; delete any that the node "
                    "made with the Qdrant API", extra={"node": node, "collection": collection, "before": sorted(before)},
                )
                return
            for name in new:
                try:
                    await self.delete_snapshot(index, collection, name)
                    logger.warning(
                        "Deleted the snapshot a failed request left on the node",
                        extra={"node": node, "collection": collection, "snapshot": name},
                    )
                except Exception:  # noqa: BLE001 - one that cannot be deleted is named, the others are still tried
                    logger.exception(
                        "A snapshot left by a failed request could not be deleted; delete it with the Qdrant API",
                        extra={"node": node, "collection": collection, "snapshot": name},
                    )
            if new:
                return
            if time.monotonic() >= deadline:
                break
            await asyncio.sleep(SNAPSHOT_CLEANUP_POLL_SECONDS)
        if patient:
            logger.warning(
                "The node may still be making a snapshot that this request gave up on; when it finishes it stays on the "
                "node unnamed. List the snapshots of the collection with the Qdrant API and delete the one that "
                "'before' does not name", extra={"node": node, "collection": collection, "before": sorted(before)},
            )

    async def delete_snapshot(self, index: int, collection: str, snapshot: str) -> None:
        await self.clients[index].delete_snapshot(collection, snapshot, wait=True)

    async def close(self) -> None:
        for client in self.clients:
            await client.close()

