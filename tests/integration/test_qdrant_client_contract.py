# SPDX-FileCopyrightText: 2026 Giovanni Manzoni
# SPDX-License-Identifier: AGPL-3.0-only

"""What the Qdrant connection module promises to a caller, an operator and a reader of the log.

``test_qdrant_pool.py`` and ``test_qdrant_snapshot_cleanup.py`` show how the pool behaves when nodes fail. This file
pins the exact values around that behaviour: the boundary of a status that may have been applied, the backoff between
retries, the settings a pool takes when it is given none, the arguments of the Qdrant clients it builds, the words of
the start-up refusals and of the warnings that tell an operator which snapshot to delete.
"""
import itertools
import logging
import warnings
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from httpx import Headers
from pydantic import SecretStr
from qdrant_client.http.exceptions import ResponseHandlingException, UnexpectedResponse

from app.config.settings import settings
from app.exceptions import QdrantStartupError
from app.repositories.qdrant import client as client_module
from app.repositories.qdrant.client import (
    NodePool,
    describe_request_failure,
    is_ambiguous,
    parse_version,
)
from tests.qdrant_fake_node import FakeQdrantNode

COLLECTION = "c"


class ScriptedClient:
    """A client whose calls come out of a script: an exception is raised, anything else is returned."""

    def __init__(self, *script: Any):
        self.script = list(script)
        self.calls = 0

    async def ping(self) -> Any:
        self.calls += 1
        outcome = self.script.pop(0) if self.script else "ok"
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


class DeletingClient:
    """A client that remembers the snapshots it was asked to delete and whether it was closed."""

    def __init__(self) -> None:
        self.deleted: list[tuple[str, str, bool]] = []
        self.closed = False

    async def delete_snapshot(self, collection: str, snapshot: str, wait: bool) -> None:
        self.deleted.append((collection, snapshot, wait))

    async def close(self) -> None:
        self.closed = True


async def ping(client: Any) -> Any:
    return await client.ping()


def unexpected(status: int | None, content: bytes = b"", reason: str = "Reason") -> UnexpectedResponse:
    return UnexpectedResponse(status, reason, content, Headers())


def pool_of(*clients: Any, key: str = "key", timeout: float = 1) -> NodePool:
    pool = NodePool(urls=[f"http://node{index}:6333" for index in range(len(clients))], api_key=key, timeout=timeout)
    pool.clients = list(clients)
    return pool


def answering(pool: NodePool, handler) -> list[httpx.Request]:
    """Make the pool's plain HTTP clients talk to ``handler``; the requests the nodes receive are returned as they come."""
    seen: list[httpx.Request] = []

    def answer(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return handler(request)

    def http(url: str, timeout: float | None = None) -> httpx.AsyncClient:
        return httpx.AsyncClient(base_url=url, transport=httpx.MockTransport(answer))

    pool.http = http  # type: ignore[method-assign]
    return seen


def warnings_of(caplog, text: str) -> list[logging.LogRecord]:
    return [record for record in caplog.records if record.getMessage() == text]


# ---- which failures may have been applied ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("status", "ambiguous"),
    [(None, False), (400, False), (404, False), (409, False), (499, False), (500, True), (502, True), (599, True)],
)
def test_an_answer_may_have_been_applied_from_status_500_up(status, ambiguous):
    assert is_ambiguous(unexpected(status)) is ambiguous


@pytest.mark.parametrize(
    ("status", "message"),
    [
        (400, "Qdrant answered HTTP 400: Bad Request"),
        (402, "Qdrant answered HTTP 402: Payment Required"),
        (404, "Qdrant answered HTTP 404: Not Found"),
        (401, "Qdrant refused the request (HTTP 401): check QDRANT_API_KEY"),
        (403, "Qdrant refused the request (HTTP 403): check QDRANT_API_KEY"),
    ],
)
def test_only_401_and_403_tell_the_operator_to_check_the_key(status, message):
    reason = message.rsplit(": ", 1)[-1]

    assert describe_request_failure(unexpected(status, b"", reason)) == message


def test_a_failure_to_connect_names_the_error_of_the_transport():
    failure = ResponseHandlingException(httpx.ConnectTimeout("slow"))

    assert describe_request_failure(failure) == (
        "Qdrant cannot be reached or did not answer in time (ConnectTimeout); check QDRANT_URL and that the node runs"
    )


@pytest.mark.parametrize(
    ("text", "parsed"),
    [
        ("1.19.2", (1, 19, 2)),
        ("10.200.3000", (10, 200, 3000)),
        ("1.x.3", (1,)),  # the numbers stop at the first piece that is not one
        ("1..2", (1,)),
        (".5", ()),
        ("v1.2", ()),
    ],
)
def test_a_server_version_stops_at_the_first_piece_without_digits(text, parsed):
    assert parse_version(text) == parsed


# ---- the clients a pool builds ----------------------------------------------------------------------------------------


def built_clients(monkeypatch) -> list[dict[str, Any]]:
    """The arguments of every ``AsyncQdrantClient`` the pool builds from now on (the client itself is the real one)."""
    built: list[dict[str, Any]] = []
    real = client_module.AsyncQdrantClient

    def spy(**kwargs):
        built.append(kwargs)
        return real(**kwargs)

    monkeypatch.setattr(client_module, "AsyncQdrantClient", spy)
    return built


def test_a_client_is_built_for_each_node_with_the_key_a_whole_second_timeout_and_no_version_check(monkeypatch):
    built = built_clients(monkeypatch)

    NodePool(urls=["http://alpha:6333", "https://beta:6334"], api_key="secret", timeout=2.2)

    assert built == [
        {"url": "http://alpha:6333", "api_key": "secret", "timeout": 3, "check_compatibility": False},
        {"url": "https://beta:6334", "api_key": "secret", "timeout": 3, "check_compatibility": False},
    ]


def test_an_empty_key_means_no_key(monkeypatch):
    built = built_clients(monkeypatch)

    pool = NodePool(urls=["http://alpha:6333"], api_key="", timeout=1)

    assert [kwargs["api_key"] for kwargs in built] == [None]
    assert pool._api_key is None


def test_only_the_user_warning_of_the_client_library_is_silenced(monkeypatch):
    real = client_module.AsyncQdrantClient

    def noisy(**kwargs):
        warnings.warn("api key over plain http", UserWarning, stacklevel=2)
        warnings.warn("something else", DeprecationWarning, stacklevel=2)
        return real(**kwargs)

    monkeypatch.setattr(client_module, "AsyncQdrantClient", noisy)

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        NodePool(urls=["http://alpha:6333"], api_key="secret", timeout=1)

    assert [str(warning.message) for warning in caught] == ["something else"]


def test_a_node_without_a_host_is_named_by_an_empty_host_in_the_plain_http_warning(caplog):
    caplog.set_level(logging.WARNING, logger=client_module.logger.name)

    NodePool(urls=["http://:6333"], api_key="secret", timeout=1)

    [warning] = warnings_of(caplog, "The Qdrant API key is sent over plain http to a remote host; use https")
    assert warning.host == ""
    assert warning.levelno == logging.WARNING


def test_a_node_without_a_key_is_never_warned_about(caplog):
    caplog.set_level(logging.WARNING, logger=client_module.logger.name)

    NodePool(urls=["http://qdrant.example.net:6333"], api_key="", timeout=1)

    assert caplog.records == []


async def test_a_pool_without_arguments_takes_the_nodes_the_key_and_the_timeout_of_the_settings(monkeypatch):
    monkeypatch.setattr(settings, "QDRANT_URL", "http://alpha:6333,http://beta:6333")
    monkeypatch.setattr(settings, "QDRANT_API_KEY", SecretStr("from-the-settings"))
    monkeypatch.setattr(settings, "QDRANT_TIMEOUT_SECONDS", 12.5)

    pool = NodePool()

    assert pool.urls == ["http://alpha:6333", "http://beta:6333"]
    assert len(pool.clients) == 2
    async with pool.http("http://alpha:6333") as http:
        assert http.headers["api-key"] == "from-the-settings"
        assert http.timeout == httpx.Timeout(12.5)


async def test_what_a_pool_is_given_wins_over_the_settings(monkeypatch):
    monkeypatch.setattr(settings, "QDRANT_URL", "http://alpha:6333")
    monkeypatch.setattr(settings, "QDRANT_API_KEY", SecretStr("from-the-settings"))
    monkeypatch.setattr(settings, "QDRANT_TIMEOUT_SECONDS", 12.5)

    pool = NodePool(urls=["http://gamma:6333"], api_key="", timeout=4)

    assert pool.urls == ["http://gamma:6333"]
    async with pool.http("http://gamma:6333") as http:
        assert "api-key" not in http.headers  # an empty key given is no key, whatever the settings hold
        assert http.timeout == httpx.Timeout(4)


async def test_the_http_client_has_the_base_url_the_key_and_the_timeout_of_the_pool_unless_one_is_asked_for():
    pool = pool_of(ScriptedClient(), key="secret", timeout=7)

    async with pool.http("http://node0:6333") as http:
        assert http.base_url == httpx.URL("http://node0:6333")
        assert http.headers["api-key"] == "secret"
        assert (b"api-key", b"secret") in http.headers.raw  # the header Qdrant documents, spelled as it documents it
        assert http.timeout == httpx.Timeout(7)
    async with pool.http("http://node0:6333", timeout=0.5) as http:
        assert http.timeout == httpx.Timeout(0.5)


async def test_a_one_node_view_shares_the_key_and_the_timeout_and_rotates_on_its_own():
    first, second = ScriptedClient("a", "b"), ScriptedClient("a", "b")
    pool = pool_of(first, second, key="secret", timeout=7)

    view = pool.only(1)

    async with view.http(view.urls[0]) as http:
        assert http.headers["api-key"] == "secret"
        assert http.timeout == httpx.Timeout(7)
    assert [await view.run(ping), await view.run(ping)] == ["a", "b"]
    assert (first.calls, second.calls) == (0, 2)  # the view never leaves its node
    assert await pool.run(ping) == "a"  # and the rotation of the pool is not the view's
    assert first.calls == 1


async def test_the_rotation_starts_each_request_at_the_next_node_and_wraps_round():
    nodes = [ScriptedClient(), ScriptedClient(), ScriptedClient()]
    pool = pool_of(*nodes)

    for _ in range(4):
        await pool.run(ping)

    assert [node.calls for node in nodes] == [2, 1, 1]


# ---- retries ----------------------------------------------------------------------------------------------------------


async def test_a_retry_waits_one_more_multiple_of_the_backoff_each_time_and_each_is_logged(monkeypatch, caplog):
    monkeypatch.setattr(client_module, "RETRY_BACKOFF_SECONDS", 0.5)
    pauses: list[float] = []

    async def pause(seconds: float) -> None:
        pauses.append(seconds)

    monkeypatch.setattr(client_module.asyncio, "sleep", pause)
    caplog.set_level(logging.WARNING, logger=client_module.logger.name)
    failure = unexpected(503)
    nodes = [ScriptedClient(failure, failure), ScriptedClient(failure, failure)]

    with pytest.raises(UnexpectedResponse) as raised:
        await pool_of(*nodes).run(ping, attempts=3)

    assert raised.value is failure
    assert pauses == [0.5, 1.0]  # none before the first attempt
    records = warnings_of(caplog, "Qdrant request failed ambiguously")
    assert [(record.attempt, record.attempts, record.error_type, record.levelno) for record in records] == [
        (1, 3, "UnexpectedResponse", logging.WARNING),
        (2, 3, "UnexpectedResponse", logging.WARNING),
        (3, 3, "UnexpectedResponse", logging.WARNING),
    ]


@pytest.mark.parametrize("attempts", [0, -3])
async def test_an_attempt_is_made_even_when_none_is_asked_for_and_only_one(attempts):
    broken, spare = ScriptedClient(TimeoutError()), ScriptedClient("answer")

    with pytest.raises(TimeoutError):
        await pool_of(broken, spare).run(ping, attempts=attempts)

    assert (broken.calls, spare.calls) == (1, 0)


async def test_the_retries_go_round_the_nodes_in_order(monkeypatch):
    monkeypatch.setattr(client_module, "RETRY_BACKOFF_SECONDS", 0)
    failure = TimeoutError()
    nodes = [ScriptedClient(failure, failure), ScriptedClient(failure, failure), ScriptedClient(failure)]

    with pytest.raises(TimeoutError):
        await pool_of(*nodes).run(ping, attempts=5)

    assert [node.calls for node in nodes] == [2, 2, 1]


async def test_closing_the_pool_closes_every_client():
    clients = [DeletingClient(), DeletingClient()]

    await pool_of(*clients).close()

    assert [client.closed for client in clients] == [True, True]


async def test_a_snapshot_is_deleted_from_the_node_asked_and_the_call_waits_for_it():
    clients = [DeletingClient(), DeletingClient()]

    await pool_of(*clients).delete_snapshot(1, "memories", "memories-1.snapshot")

    assert [client.deleted for client in clients] == [[], [("memories", "memories-1.snapshot", True)]]


# ---- the version of the servers ---------------------------------------------------------------------------------------


async def test_the_version_of_each_node_is_read_from_its_root_document_in_the_order_of_the_nodes():
    pool = pool_of(ScriptedClient(), ScriptedClient())
    versions = {"node0": "1.19.2", "node1": "1.20.0"}
    seen = answering(pool, lambda request: httpx.Response(200, json={"title": "qdrant", "version": versions[request.url.host]}))

    assert await pool.server_versions() == [("http://node0:6333", "1.19.2"), ("http://node1:6333", "1.20.0")]
    assert [(request.method, request.url.host, request.url.path) for request in seen] == [
        ("GET", "node0", "/"), ("GET", "node1", "/"),
    ]


def refuse(request: httpx.Request) -> httpx.Response:
    raise httpx.ConnectError("refused", request=request)


@pytest.mark.parametrize(
    ("handler", "error"),
    [
        (lambda _request: httpx.Response(500, json={"version": "1.19.2"}), "HTTPStatusError"),  # an answer, but an error
        (lambda _request: httpx.Response(200, json={"title": "qdrant"}), "KeyError"),
        (lambda _request: httpx.Response(200, content=b"<html>proxy</html>"), "JSONDecodeError"),
        (refuse, "ConnectError"),
    ],
    ids=["error-status", "no-version", "not-json", "refused"],
)
async def test_a_node_that_does_not_report_its_version_is_named_with_the_kind_of_failure(handler, error):
    pool = pool_of(ScriptedClient())
    answering(pool, handler)

    with pytest.raises(QdrantStartupError) as refused:
        await pool.server_versions()

    assert str(refused.value) == f"Qdrant at http://node0:6333 cannot be reached or did not report its version ({error})"
    assert type(refused.value.__cause__).__name__ == error


async def test_a_silent_node_is_logged_by_address_when_the_others_are_asked_to_go_on(caplog):
    caplog.set_level(logging.WARNING, logger=client_module.logger.name)
    pool = pool_of(ScriptedClient(), ScriptedClient())
    answering(pool, lambda request: httpx.Response(200, json={"version": "1.19.2"}) if request.url.host == "node1" else httpx.Response(503))

    assert await pool.server_versions(skip_silent=True) == [("http://node1:6333", "1.19.2")]

    [warning] = warnings_of(caplog, "A Qdrant node did not answer; going on with the others")
    assert (warning.node, warning.levelno) == ("http://node0:6333", logging.WARNING)


async def test_the_nodes_are_asked_for_their_versions_strictly_unless_told_otherwise():
    pool = pool_of(ScriptedClient(), ScriptedClient())
    answering(pool, lambda request: httpx.Response(200, json={"version": "1.19.2"}) if request.url.host == "node1" else httpx.Response(503))

    with pytest.raises(QdrantStartupError, match=r"Qdrant at http://node0:6333 cannot be reached"):
        await pool.server_versions()


@pytest.mark.parametrize(
    ("version", "too_old"),
    [("1.18.9", True), ("1.19", True), ("1.19.0", False), ("1.19.1", False), ("1.20.0", False), ("2.0.0", False)],
)
async def test_the_minimum_server_version_itself_is_supported(version, too_old, monkeypatch):
    monkeypatch.setattr(client_module, "MINIMUM_SERVER_VERSION", (1, 19, 0))
    pool = pool_of(ScriptedClient())
    answering(pool, lambda _request: httpx.Response(200, json={"version": version}))

    if too_old:
        with pytest.raises(QdrantStartupError) as refused:
            await pool.require_supported_server()
        assert str(refused.value) == f"Qdrant {version} at http://node0:6333 is too old: version 1.19.0 or later is required"
    else:
        await pool.require_supported_server()


async def test_the_refusal_of_an_old_server_names_the_minimum_with_all_its_parts(monkeypatch):
    monkeypatch.setattr(client_module, "MINIMUM_SERVER_VERSION", (3, 20, 7))
    pool = pool_of(ScriptedClient())
    answering(pool, lambda _request: httpx.Response(200, json={"version": "3.20.6"}))

    with pytest.raises(QdrantStartupError, match=r"is too old: version 3\.20\.7 or later is required$"):
        await pool.require_supported_server()


# ---- snapshots on a node ----------------------------------------------------------------------------------------------


@pytest.fixture
def node(monkeypatch, caplog):
    """A node to take snapshots on; the client gives up on a request after 0.3 s and looks for a late snapshot every 0.1 s."""
    monkeypatch.setattr(client_module, "SNAPSHOT_TIMEOUT_SECONDS", 0.3)
    monkeypatch.setattr(client_module, "SNAPSHOT_CLEANUP_POLL_SECONDS", 0.1)
    monkeypatch.setattr(client_module, "SNAPSHOT_CLEANUP_SECONDS", 20.0)
    caplog.set_level(logging.WARNING, logger=client_module.logger.name)
    with FakeQdrantNode() as fake:
        yield fake


@pytest.fixture
async def pool(node):
    pool = NodePool([node.url], api_key="", timeout=0.2)
    yield pool
    await pool.close()


async def test_a_snapshot_is_requested_with_post_and_the_wait_parameter_under_the_longer_of_the_two_timeouts(node):
    pool = NodePool([node.url], api_key="", timeout=5000.0)
    timeouts: list[float | None] = []
    requests: list[httpx.Request] = []
    http = pool.http

    def recording(url, timeout=None):
        timeouts.append(timeout)
        client = http(url, timeout)

        async def seen(request: httpx.Request) -> None:
            requests.append(request)

        client.event_hooks["request"].append(seen)
        return client

    pool.http = recording  # type: ignore[method-assign]
    try:
        assert await pool.create_snapshot(0, COLLECTION) == f"{COLLECTION}-1.snapshot"
    finally:
        await pool.close()

    assert timeouts == [5000.0]
    [request] = requests
    assert (request.method, request.url.path, dict(request.url.params)) == (
        "POST", f"/collections/{COLLECTION}/snapshots", {"wait": "true"},
    )


async def test_a_pool_that_waits_less_than_a_snapshot_takes_waits_the_snapshot_timeout(node, pool):
    timeouts: list[float | None] = []
    http = pool.http

    def recording(url, timeout=None):
        timeouts.append(timeout)
        return http(url, timeout)

    pool.http = recording  # type: ignore[method-assign]

    await pool.create_snapshot(0, COLLECTION)

    assert timeouts == [client_module.SNAPSHOT_TIMEOUT_SECONDS]


async def test_the_snapshots_a_failed_request_left_are_named_with_the_node_the_collection_and_the_snapshot(node, pool, caplog):
    node.create_status = 500
    node.snapshots = {COLLECTION: ["older.snapshot"]}

    with pytest.raises(httpx.HTTPStatusError):
        await pool.create_snapshot(0, COLLECTION)

    [deleted] = warnings_of(caplog, "Deleted the snapshot a failed request left on the node")
    assert (deleted.node, deleted.collection, deleted.snapshot, deleted.levelno) == (
        node.url, COLLECTION, f"{COLLECTION}-1.snapshot", logging.WARNING,
    )
    assert node.snapshots == {COLLECTION: ["older.snapshot"]}


async def test_a_snapshot_that_cannot_be_deleted_is_an_error_with_its_traceback_and_does_not_replace_the_failure(node, pool, caplog):
    node.create_status = 500
    node.delete_fails = True

    with pytest.raises(httpx.HTTPStatusError):
        await pool.create_snapshot(0, COLLECTION)

    [failure] = warnings_of(
        caplog, "A snapshot left by a failed request could not be deleted; delete it with the Qdrant API",
    )
    assert (failure.node, failure.collection, failure.snapshot, failure.levelno) == (
        node.url, COLLECTION, f"{COLLECTION}-1.snapshot", logging.ERROR,
    )
    assert failure.exc_info is not None


async def test_a_node_that_will_not_list_its_snapshots_after_a_failure_is_named_with_what_was_there_before(node, pool, caplog):
    node.create_status = 500
    node.list_fails_after = 1
    node.snapshots = {COLLECTION: ["b.snapshot", "a.snapshot"]}

    with pytest.raises(httpx.HTTPStatusError):
        await pool.create_snapshot(0, COLLECTION)

    [warning] = warnings_of(
        caplog,
        "The snapshots of a collection could not be listed after a failed snapshot; delete any that the node made with "
        "the Qdrant API",
    )
    assert (warning.node, warning.collection, warning.before, warning.levelno) == (
        node.url, COLLECTION, ["a.snapshot", "b.snapshot"], logging.WARNING,
    )


async def test_a_node_that_will_not_list_its_snapshots_before_is_named_in_the_warning(node, pool, caplog):
    node.list_fails_after = 0

    await pool.create_snapshot(0, COLLECTION)

    [warning] = warnings_of(
        caplog,
        "The snapshots of a collection could not be listed before taking one, so a failed snapshot cannot be cleaned up",
    )
    assert (warning.node, warning.collection, warning.levelno) == (node.url, COLLECTION, logging.WARNING)


async def test_a_snapshot_that_never_appears_is_named_to_the_operator_with_the_listing_to_compare_against(
    node, pool, monkeypatch, caplog,
):
    monkeypatch.setattr(client_module, "SNAPSHOT_CLEANUP_SECONDS", 0.4)
    node.snapshots = {COLLECTION: ["b.snapshot", "a.snapshot"]}
    node.create_delay = 60.0  # released when the test ends

    with pytest.raises(httpx.ReadTimeout):
        await pool.create_snapshot(0, COLLECTION)

    [warning] = warnings_of(
        caplog,
        "The node may still be making a snapshot that this request gave up on; when it finishes it stays on the node "
        "unnamed. List the snapshots of the collection with the Qdrant API and delete the one that 'before' does not name",
    )
    assert (warning.node, warning.collection, warning.before, warning.levelno) == (
        node.url, COLLECTION, ["a.snapshot", "b.snapshot"], logging.WARNING,
    )
    assert node.listings > 3  # it looked again every poll interval until the time was up


async def test_a_node_that_answered_with_an_error_is_looked_at_once_and_not_waited_on(node, pool):
    node.create_status = 404
    node.made_before_error = False

    with pytest.raises(httpx.HTTPStatusError):
        await pool.create_snapshot(0, COLLECTION)

    assert node.listings == 2  # before the request, and once after it


async def test_a_failure_that_was_answered_is_looked_at_once_even_when_no_time_has_passed_since_it_began(node, pool, monkeypatch):
    node.create_status = 404
    node.made_before_error = False
    ticks = itertools.chain([100.0, 100.0], itertools.count(200.0, 100.0))  # the deadline, the first look, then time moves on
    monkeypatch.setattr(client_module, "time", SimpleNamespace(monotonic=lambda: next(ticks)))

    with pytest.raises(httpx.HTTPStatusError):
        await pool.create_snapshot(0, COLLECTION)

    assert node.listings == 2
