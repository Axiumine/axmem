"""The Qdrant connections: failover between nodes, retries of ambiguous failures, the server version check.

The faults are injected between the pool and its clients (a client whose calls raise what the library raises
on a reset connection or a 5xx answer); everything that does not need a fault runs against the real node.
"""
import logging
from typing import Any

import pytest
from qdrant_client.http.exceptions import ResponseHandlingException, UnexpectedResponse

from app.exceptions import QdrantStartupError
from app.repositories.qdrant import client as client_module
from app.repositories.qdrant.client import NodePool, is_ambiguous, parse_version


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


def unexpected(status: int) -> UnexpectedResponse:
    return UnexpectedResponse(status, "reason", b"", None)


def pool_of(*clients: ScriptedClient) -> NodePool:
    pool = NodePool(urls=[f"http://node{index}:6333" for index in range(len(clients))], api_key="key", timeout=1)
    pool.clients = list(clients)
    return pool


@pytest.fixture(autouse=True)
def no_waiting(monkeypatch):
    monkeypatch.setattr(client_module, "RETRY_BACKOFF_SECONDS", 0)


async def ping(client: Any) -> Any:
    return await client.ping()


@pytest.mark.parametrize(
    ("text", "parsed"),
    [("1.19.2", (1, 19, 2)), ("1.19.0-rc1", (1, 19, 0)), ("2.0", (2, 0)), ("1.x", (1,)), ("", ())],
)
def test_a_server_version_is_read_by_its_leading_numbers(text, parsed):
    assert parse_version(text) == parsed


@pytest.mark.parametrize(
    ("failure", "ambiguous"),
    [
        (ResponseHandlingException(Exception("connection reset")), True),
        (TimeoutError(), True),
        (unexpected(500), True),
        (unexpected(503), True),
        (unexpected(409), False),
        (unexpected(404), False),
        (ValueError("bad"), False),
    ],
)
def test_only_transport_failures_and_server_errors_are_ambiguous(failure, ambiguous):
    assert is_ambiguous(failure) is ambiguous


async def test_a_call_that_succeeds_is_made_once():
    node = ScriptedClient("answer")

    assert await pool_of(node).run(ping) == "answer"
    assert node.calls == 1


async def test_an_ambiguous_failure_is_retried_on_the_next_node():
    broken, healthy = ScriptedClient(ResponseHandlingException(Exception("reset"))), ScriptedClient("answer")
    pool = pool_of(broken, healthy)

    assert await pool.run(ping, attempts=2) == "answer"
    assert (broken.calls, healthy.calls) == (1, 1)


async def test_an_ambiguous_failure_is_raised_once_the_attempts_are_used(caplog):
    failure = unexpected(502)
    nodes = [ScriptedClient(failure, failure), ScriptedClient(failure, failure)]
    caplog.set_level(logging.WARNING, logger=client_module.logger.name)

    with pytest.raises(UnexpectedResponse) as raised:
        await pool_of(*nodes).run(ping, attempts=3)

    assert raised.value is failure
    assert sum(node.calls for node in nodes) == 3
    assert [record.getMessage() for record in caplog.records].count("Qdrant request failed ambiguously") == 3


async def test_one_attempt_is_made_unless_more_are_asked_for():
    broken, spare = ScriptedClient(TimeoutError()), ScriptedClient("answer")

    with pytest.raises(TimeoutError):
        await pool_of(broken, spare).run(ping)

    assert spare.calls == 0


async def test_a_definite_refusal_is_not_retried():
    refusing, spare = ScriptedClient(unexpected(409)), ScriptedClient("answer")

    with pytest.raises(UnexpectedResponse):
        await pool_of(refusing, spare).run(ping, attempts=3)

    assert spare.calls == 0


async def test_requests_start_at_a_different_node_each_time():
    first, second = ScriptedClient("a", "b"), ScriptedClient("a", "b")
    pool = pool_of(first, second)

    await pool.run(ping)
    await pool.run(ping)

    assert (first.calls, second.calls) == (1, 1)


def test_a_one_node_view_shares_that_nodes_client():
    first, second = ScriptedClient(), ScriptedClient()
    pool = pool_of(first, second)

    view = pool.only(1)

    assert view.clients == [second]
    assert view.urls == ["http://node1:6333"]


async def test_the_http_client_carries_the_api_key_only_when_there_is_one():
    keyed = pool_of(ScriptedClient())
    open_pool = NodePool(urls=["http://node0:6333"], api_key="", timeout=1)

    async with keyed.http("http://node0:6333") as http:
        assert http.headers["api-key"] == "key"
    async with open_pool.http("http://node0:6333") as http:
        assert "api-key" not in http.headers
    await open_pool.close()


def test_a_key_sent_over_plain_http_to_a_remote_host_is_warned_about(caplog):
    caplog.set_level(logging.WARNING, logger=client_module.logger.name)

    NodePool(urls=["http://qdrant.example.net:6333"], api_key="key", timeout=1)
    NodePool(urls=["http://127.0.0.1:6333"], api_key="key", timeout=1)
    NodePool(urls=["https://qdrant.example.net:6333"], api_key="key", timeout=1)

    warnings = [record for record in caplog.records if "plain http" in record.getMessage()]
    assert [record.host for record in warnings] == ["qdrant.example.net"]


async def test_the_pool_reports_the_version_of_the_node(qdrant_env):
    pool = NodePool()
    try:
        [(url, version)] = await pool.server_versions()

        assert url == qdrant_env.QDRANT_URL
        assert parse_version(version) >= client_module.MINIMUM_SERVER_VERSION
        await pool.require_supported_server()
    finally:
        await pool.close()


async def test_a_server_older_than_the_minimum_is_refused(qdrant_env, monkeypatch):
    monkeypatch.setattr(client_module, "MINIMUM_SERVER_VERSION", (99, 0, 0))
    pool = NodePool()
    try:
        with pytest.raises(QdrantStartupError, match=r"is too old: version 99\.0\.0 or later is required"):
            await pool.require_supported_server()
    finally:
        await pool.close()


async def test_a_node_that_cannot_be_reached_stops_the_start_with_its_address():
    pool = NodePool(urls=["http://127.0.0.1:9"], api_key="key", timeout=1)
    try:
        with pytest.raises(QdrantStartupError, match=r"Qdrant at http://127\.0\.0\.1:9 cannot be reached"):
            await pool.require_supported_server()
    finally:
        await pool.close()
