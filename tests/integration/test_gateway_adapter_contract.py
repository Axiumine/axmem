# SPDX-FileCopyrightText: 2026 Giovanni Manzoni
# SPDX-License-Identifier: AGPL-3.0-only

"""The embeddings gateway adapter, word for word: the lines an operator reads, the wire format and the refusals.

The adapter runs against an in-process gateway (``httpx.MockTransport``); the tests pin the exact texts of the errors
that stop a start or a call, the log records of the start and of every retry, and the names and timeouts of the
requests as they leave the client.
"""
import json
import logging

import httpx
import pytest
from pydantic import SecretStr

from app.config.settings import settings
from app.exceptions import EmbeddingUnavailableError
from app.repositories.embeddings import gateway_adapter
from app.repositories.embeddings.gateway_adapter import (
    GatewayEmbeddingError,
    GatewayEmbeddingsAdapter,
)

WIDTH = 4
ENDPOINT = "https://gateway.example.test/api/v1/embeddings"
STANDARD_ATTRIBUTES = set(logging.makeLogRecord({}).__dict__) | {"message", "asctime"}


class Gateway:
    """Answers the requests it is given from a script, then with one vector of ``WIDTH`` numbers per text."""

    def __init__(self, *script: httpx.Response):
        self.script = list(script)
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.script:
            return self.script.pop(0)
        texts = json.loads(request.content)["input"]
        return httpx.Response(200, json={"embeddings": [[1.0] * WIDTH for _ in texts]})


class Sleeps:
    def __init__(self):
        self.waits: list[float] = []

    async def __call__(self, seconds: float) -> None:
        self.waits.append(seconds)


@pytest.fixture(autouse=True)
def gateway_settings(monkeypatch, caplog):
    for name, value in {
        "GATEWAY_URL": "https://gateway.example.test",
        "GATEWAY_PATH": "/api/v1/embeddings",
        "GATEWAY_TOKEN": SecretStr("tok-9f3a"),
        "GATEWAY_AUTH_HEADER": "Authorization",
        "GATEWAY_TENANT_HEADER": "X-Tenant-Id",
        "GATEWAY_TENANT": "",
        "GATEWAY_BACKEND": "local",
        "GATEWAY_MODEL": "embed-small",
        "GATEWAY_AGENT_ID": "axmem",
        "GATEWAY_METADATA": "{}",
        "GATEWAY_SUPPORTS_DIMENSIONS": False,
        "EMBEDDING_DIMENSIONS": WIDTH,
        "EMBEDDING_REQUEST_TIMEOUT_SECONDS": 17.0,
    }.items():
        monkeypatch.setattr(settings, name, value)
    monkeypatch.setattr(gateway_adapter.logger, "disabled", False)  # the logging set-up of an app that started earlier disables it
    with caplog.at_level(logging.INFO, logger=gateway_adapter.logger.name):
        yield


def build(gateway: Gateway) -> tuple[GatewayEmbeddingsAdapter, Sleeps]:
    sleeps = Sleeps()
    return GatewayEmbeddingsAdapter(transport=httpx.MockTransport(gateway), sleep=sleeps), sleeps


def extras(record: logging.LogRecord) -> dict:
    """The fields a call added to the record with ``extra=``."""
    return {name: value for name, value in record.__dict__.items() if name not in STANDARD_ATTRIBUTES}


# --------------------------------------------------------------------------
# What a refused configuration says
# --------------------------------------------------------------------------
def test_every_missing_setting_is_named_after_the_four_that_are_needed(monkeypatch):
    for name in ("GATEWAY_URL", "GATEWAY_BACKEND", "GATEWAY_MODEL", "GATEWAY_AGENT_ID"):
        monkeypatch.setattr(settings, name, " ")

    with pytest.raises(ValueError) as refused:
        GatewayEmbeddingsAdapter()

    assert str(refused.value) == (
        "GATEWAY_URL, GATEWAY_BACKEND, GATEWAY_MODEL and GATEWAY_AGENT_ID must be configured for "
        "GatewayEmbeddingsAdapter; missing: GATEWAY_URL, GATEWAY_BACKEND, GATEWAY_MODEL, GATEWAY_AGENT_ID"
    )


@pytest.mark.parametrize(("url", "message"), [
    ("http://host:abc", "GATEWAY_URL is not a valid URL"),
    ("gateway.example.test", "GATEWAY_URL must be an http:// or https:// URL with a host"),
    ("ftp://gateway.example.test", "GATEWAY_URL must be an http:// or https:// URL with a host"),
    ("https://", "GATEWAY_URL must be an http:// or https:// URL with a host"),
    ("https://user:pass@gateway.example.test",
     "GATEWAY_URL must not carry credentials, a query or a fragment: set GATEWAY_TOKEN and GATEWAY_PATH instead"),
    ("https://gateway.example.test/?token=abc",
     "GATEWAY_URL must not carry credentials, a query or a fragment: set GATEWAY_TOKEN and GATEWAY_PATH instead"),
    ("https://gateway.example.test/#frag",
     "GATEWAY_URL must not carry credentials, a query or a fragment: set GATEWAY_TOKEN and GATEWAY_PATH instead"),
])
def test_a_url_that_cannot_be_used_says_why(monkeypatch, url, message):
    monkeypatch.setattr(settings, "GATEWAY_URL", url)

    with pytest.raises(ValueError) as refused:
        GatewayEmbeddingsAdapter()

    assert str(refused.value) == message


def test_a_plain_http_gateway_is_accepted(monkeypatch):
    monkeypatch.setattr(settings, "GATEWAY_URL", "http://gateway.internal:8080")

    assert GatewayEmbeddingsAdapter().endpoint == "http://gateway.internal:8080/api/v1/embeddings"


def test_only_slashes_are_stripped_where_the_url_and_the_path_meet(monkeypatch):
    monkeypatch.setattr(settings, "GATEWAY_URL", "https://gateway.example.test/GX/")
    monkeypatch.setattr(settings, "GATEWAY_PATH", "/XQ/embed")

    assert GatewayEmbeddingsAdapter().endpoint == "https://gateway.example.test/GX/XQ/embed"


def test_metadata_that_is_not_an_object_of_strings_says_so(monkeypatch):
    monkeypatch.setattr(settings, "GATEWAY_METADATA", '{"retries": 3}')

    with pytest.raises(ValueError) as refused:
        GatewayEmbeddingsAdapter()

    assert str(refused.value) == "GATEWAY_METADATA must be a JSON object of string values"


@pytest.mark.parametrize(("setting", "value"), [
    ("GATEWAY_TOKEN", SecretStr("tøken")),
    ("GATEWAY_TENANT", "acme\nx"),
])
def test_a_credential_that_is_not_printable_ascii_says_so_without_repeating_it(monkeypatch, setting, value):
    monkeypatch.setattr(settings, setting, value)

    with pytest.raises(ValueError) as refused:
        GatewayEmbeddingsAdapter()

    assert str(refused.value) == "GATEWAY_TOKEN and GATEWAY_TENANT must be printable ASCII"


# --------------------------------------------------------------------------
# The line an operator reads when the adapter starts
# --------------------------------------------------------------------------
@pytest.mark.parametrize("supports_dimensions", [True, False])
def test_the_start_up_line_names_the_endpoint_backend_model_and_dimensions_setting(
    monkeypatch, caplog, supports_dimensions,
):
    monkeypatch.setattr(settings, "GATEWAY_SUPPORTS_DIMENSIONS", supports_dimensions)

    GatewayEmbeddingsAdapter()

    (record,) = [r for r in caplog.records if r.getMessage() == "Initialising embeddings gateway adapter"]
    assert record.levelno == logging.INFO
    assert extras(record) == {
        "gateway_url": ENDPOINT,
        "gateway_backend": "local",
        "gateway_model": "embed-small",
        "supports_dimensions": supports_dimensions,
    }


# --------------------------------------------------------------------------
# The wire format
# --------------------------------------------------------------------------
async def test_a_request_carries_the_two_key_headers_by_their_documented_names_and_the_configured_timeout():
    gateway = Gateway()
    adapter, _ = build(gateway)

    await adapter.generate_embedding("hello")

    (request,) = gateway.requests
    names = {name for name, _ in request.headers.raw}
    assert {b"Authorization", b"X-Idempotency-Key", b"X-Correlation-Id"} <= names
    assert request.extensions["timeout"] == {"connect": 17.0, "read": 17.0, "write": 17.0, "pool": 17.0}


async def test_a_call_that_has_no_text_sends_nothing_and_a_call_with_texts_sends_them_all():
    gateway = Gateway()
    adapter, _ = build(gateway)

    assert await adapter.generate_embeddings([]) == []
    assert await adapter.generate_embeddings(["a", "b"]) == [[1.0] * WIDTH] * 2
    assert len(gateway.requests) == 1


# --------------------------------------------------------------------------
# What a refusal or a bad answer says
# --------------------------------------------------------------------------
@pytest.mark.parametrize("response", [
    httpx.Response(401, content=b"not json"),
    httpx.Response(401, json=["a", "list"]),
    httpx.Response(401, json={"message": "no code"}),
    httpx.Response(401, json={"code": 7}),
    httpx.Response(401, json={"code": "lower case and spaces"}),
])
async def test_a_refusal_without_a_usable_code_says_the_code_is_unknown(response):
    adapter, _ = build(Gateway(response))

    with pytest.raises(GatewayEmbeddingError) as refused:
        await adapter.generate_embedding("hello")

    assert str(refused.value) == "Embeddings gateway refused the request: HTTP 401, code unknown"


async def test_a_refusal_names_its_status_and_code():
    adapter, _ = build(Gateway(httpx.Response(403, json={"code": "BUDGET_EXCEEDED"})))

    with pytest.raises(GatewayEmbeddingError) as refused:
        await adapter.generate_embedding("hello")

    assert str(refused.value) == "Embeddings gateway refused the request: HTTP 403, code BUDGET_EXCEEDED"


async def test_a_gateway_that_stays_unavailable_says_how_often_it_was_asked_and_what_the_last_answer_was():
    adapter, _ = build(Gateway(*[httpx.Response(503, json={"code": "BACKEND_UNAVAILABLE"})] * 4))

    with pytest.raises(EmbeddingUnavailableError) as refused:
        await adapter.generate_embedding("hello")

    assert str(refused.value) == (
        "Embeddings gateway failed after 4 attempts, last failure: HTTP 503, code BACKEND_UNAVAILABLE"
    )


async def test_each_retry_is_logged_with_its_failure_attempt_wait_and_endpoint(caplog):
    gateway = Gateway(
        httpx.Response(503, json={"code": "BACKEND_UNAVAILABLE"}),
        httpx.Response(429, json={"code": "BACKEND_RATE_LIMITED"}, headers={"Retry-After": "3"}),
    )
    adapter, sleeps = build(gateway)

    await adapter.generate_embedding("hello")

    retries = [r for r in caplog.records if r.getMessage() == "Embeddings gateway request failed, retrying"]
    assert [record.levelno for record in retries] == [logging.WARNING, logging.WARNING]
    assert [extras(record) for record in retries] == [
        {"failure": "HTTP 503, code BACKEND_UNAVAILABLE", "attempt": 1, "retry_in_seconds": 0.5, "gateway_url": ENDPOINT},
        {"failure": "HTTP 429, code BACKEND_RATE_LIMITED", "attempt": 2, "retry_in_seconds": 3.0, "gateway_url": ENDPOINT},
    ]
    assert sleeps.waits == [0.5, 3.0]


@pytest.mark.parametrize("answer", [
    httpx.Response(200, content=b"<html>not json</html>"),
    httpx.Response(200, json=["not", "an", "object"]),
    httpx.Response(200, json={"vectors": []}),
])
async def test_an_answer_without_an_embeddings_list_says_so(answer):
    adapter, _ = build(Gateway(answer))

    with pytest.raises(GatewayEmbeddingError) as refused:
        await adapter.generate_embedding("hello")

    assert str(refused.value) == "Embeddings gateway answered without an embeddings list"


async def test_a_vector_of_another_width_says_how_to_set_the_width_right():
    adapter, _ = build(Gateway(httpx.Response(200, json={"embeddings": [[1.0] * 8]})))

    with pytest.raises(EmbeddingUnavailableError) as refused:
        await adapter.generate_embedding("hello")

    assert str(refused.value) == (
        "Embeddings gateway returned 8-dimensional vectors but EMBEDDING_DIMENSIONS is 4. "
        "Set EMBEDDING_DIMENSIONS to the width the gateway's model produces (or GATEWAY_SUPPORTS_DIMENSIONS=true "
        "if it can produce the configured width), then run --re-embed."
    )
