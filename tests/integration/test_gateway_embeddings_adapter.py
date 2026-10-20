"""The optional HTTP embeddings gateway provider, against a small in-process fake gateway.

The fake serves the request/response contract the adapter speaks (strict body, 1 to 256 inputs, vectors in
input order) and can be scripted to fail first, so retries, headers and validation run without a network.
"""
import json
import logging
import uuid
from datetime import UTC, datetime, timedelta
from email.utils import format_datetime
from unittest.mock import AsyncMock

import httpx
import pytest
from pydantic import SecretStr, ValidationError

from app.config.settings import Settings, settings
from app.models.memory_models import Memory
from app.protocols.embedding_protocol import EMBEDDING_BATCH_LIMIT
from app.repositories.embeddings.gateway_adapter import (
    GatewayEmbeddingError,
    GatewayEmbeddingsAdapter,
)
from app.services.re_embedding_service import ReEmbeddingService

TOKEN = "tok-9f3a-secret"
WIDTH = 4
ALLOWED_FIELDS = {"request_id", "agent_id", "backend", "model", "input", "dimensions", "metadata"}


def vector_for(text: str, width: int = WIDTH) -> list[float]:
    """A deterministic vector that depends on the text, so ordering mistakes show."""
    return [float(len(text)) + index for index in range(width)]


def error_response(status: int, code: str, **headers: str) -> httpx.Response:
    return httpx.Response(status, json={"code": code, "message": "refused"}, headers=headers)


class FakeGateway:
    """Serves the contract; `script` entries (responses or exceptions) are served before any success."""

    def __init__(self, width: int = WIDTH, script: list | None = None):
        self.width = width
        self.script = list(script or [])
        self.requests: list[httpx.Request] = []
        self.bodies: list[dict] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        self.requests.append(request)
        self.bodies.append(body)
        if self.script:
            item = self.script.pop(0)
            if isinstance(item, Exception):
                raise item
            return item
        if set(body) - ALLOWED_FIELDS or not 1 <= len(body.get("input", [])) <= EMBEDDING_BATCH_LIMIT:
            return error_response(422, "VALIDATION_FAILED")
        return httpx.Response(200, json={
            "request_id": body["request_id"],
            "backend": body["backend"],
            "model": body["model"],
            "dimensions": self.width,
            "embeddings": [vector_for(text, self.width) for text in body["input"]],
            "usage": {"input_texts": len(body["input"])},
        })


class Sleeps:
    """Records the waits the adapter asks for instead of sleeping."""

    def __init__(self):
        self.waits: list[float] = []

    async def __call__(self, seconds: float) -> None:
        self.waits.append(seconds)


@pytest.fixture(autouse=True)
def gateway_settings(monkeypatch):
    for name, value in {
        "GATEWAY_URL": "https://gateway.example.test",
        "GATEWAY_PATH": "/api/v1/embeddings",
        "GATEWAY_TOKEN": SecretStr(TOKEN),
        "GATEWAY_AUTH_HEADER": "Authorization",
        "GATEWAY_TENANT_HEADER": "X-Tenant-Id",
        "GATEWAY_TENANT": "",
        "GATEWAY_BACKEND": "local",
        "GATEWAY_MODEL": "embed-small",
        "GATEWAY_AGENT_ID": "axmem",
        "GATEWAY_METADATA": {},
        "GATEWAY_SUPPORTS_DIMENSIONS": False,
        "EMBEDDING_DIMENSIONS": WIDTH,
        "EMBEDDING_REQUEST_TIMEOUT_SECONDS": 5.0,
    }.items():
        monkeypatch.setattr(settings, name, value)


def build(gateway: FakeGateway, **kwargs) -> tuple[GatewayEmbeddingsAdapter, Sleeps]:
    sleeps = Sleeps()
    adapter = GatewayEmbeddingsAdapter(transport=httpx.MockTransport(gateway), sleep=sleeps, **kwargs)
    return adapter, sleeps


# --------------------------------------------------------------------------
# Success
# --------------------------------------------------------------------------
async def test_single_text_returns_its_vector_and_sends_the_contract_body():
    gateway = FakeGateway()
    adapter, _ = build(gateway)

    assert await adapter.generate_embedding("hello") == vector_for("hello")

    (request,) = gateway.requests
    assert request.method == "POST"
    assert str(request.url) == "https://gateway.example.test/api/v1/embeddings"
    body = gateway.bodies[0]
    assert uuid.UUID(body.pop("request_id"))
    assert body == {
        "agent_id": "axmem", "backend": "local", "model": "embed-small", "input": ["hello"], "metadata": {},
    }


async def test_a_batch_comes_back_in_input_order():
    adapter, _ = build(FakeGateway())
    texts = ["a", "bbb", "cc", "dddd"]

    assert await adapter.generate_embeddings(texts) == [vector_for(text) for text in texts]


async def test_an_empty_batch_makes_no_request():
    gateway = FakeGateway()
    adapter, _ = build(gateway)

    assert await adapter.generate_embeddings([]) == []
    assert gateway.requests == []


@pytest.mark.parametrize(("count", "chunk_sizes"), [
    (1, [1]),
    (256, [256]),
    (257, [256, 1]),
    (600, [256, 256, 88]),
])
async def test_batches_are_chunked_at_256_and_keep_their_order(count, chunk_sizes):
    gateway = FakeGateway()
    adapter, _ = build(gateway)
    texts = [f"text {index}" * (1 + index % 3) for index in range(count)]

    vectors = await adapter.generate_embeddings(texts)

    assert vectors == [vector_for(text) for text in texts]
    assert [len(body["input"]) for body in gateway.bodies] == chunk_sizes


async def test_ids_and_headers_for_chunks_of_one_call():
    gateway = FakeGateway()
    adapter, _ = build(gateway)

    await adapter.generate_embeddings(["x"] * 600)

    keys = [request.headers["X-Idempotency-Key"] for request in gateway.requests]
    correlations = {request.headers["X-Correlation-Id"] for request in gateway.requests}
    request_ids = [body["request_id"] for body in gateway.bodies]
    assert len(set(keys)) == len(keys) == 3
    assert len(set(request_ids)) == 3
    assert len(correlations) == 1
    assert all(uuid.UUID(value) for value in [*keys, *correlations, *request_ids])


async def test_each_call_has_its_own_correlation_id():
    gateway = FakeGateway()
    adapter, _ = build(gateway)

    await adapter.generate_embedding("one")
    await adapter.generate_embedding("two")

    assert gateway.requests[0].headers["X-Correlation-Id"] != gateway.requests[1].headers["X-Correlation-Id"]


async def test_metadata_and_dimensions_are_sent_when_configured(monkeypatch):
    monkeypatch.setattr(settings, "GATEWAY_METADATA", {"team": "memory", "env": "test"})
    monkeypatch.setattr(settings, "GATEWAY_SUPPORTS_DIMENSIONS", True)
    gateway = FakeGateway()
    adapter, _ = build(gateway)

    await adapter.generate_embedding("hello")

    assert gateway.bodies[0]["metadata"] == {"team": "memory", "env": "test"}
    assert gateway.bodies[0]["dimensions"] == WIDTH


async def test_path_and_url_are_joined_whatever_the_slashes(monkeypatch):
    monkeypatch.setattr(settings, "GATEWAY_URL", "https://gateway.example.test:8443/edge/")
    monkeypatch.setattr(settings, "GATEWAY_PATH", "v2/embed")
    gateway = FakeGateway()
    adapter, _ = build(gateway)

    await adapter.generate_embedding("hello")

    assert str(gateway.requests[0].url) == "https://gateway.example.test:8443/edge/v2/embed"


# --------------------------------------------------------------------------
# Credential and tenant headers
# --------------------------------------------------------------------------
async def test_the_default_credential_header_is_a_bearer_token():
    gateway = FakeGateway()
    adapter, _ = build(gateway)

    await adapter.generate_embedding("hello")

    headers = gateway.requests[0].headers
    assert headers["Authorization"] == f"Bearer {TOKEN}"
    assert "X-Tenant-Id" not in headers


async def test_another_credential_header_gets_the_raw_token(monkeypatch):
    monkeypatch.setattr(settings, "GATEWAY_AUTH_HEADER", "X-Api-Key")
    gateway = FakeGateway()
    adapter, _ = build(gateway)

    await adapter.generate_embedding("hello")

    headers = gateway.requests[0].headers
    assert headers["X-Api-Key"] == TOKEN
    assert "Authorization" not in headers


async def test_no_token_means_no_credential_header(monkeypatch):
    monkeypatch.setattr(settings, "GATEWAY_TOKEN", SecretStr(""))
    gateway = FakeGateway()
    adapter, _ = build(gateway)

    await adapter.generate_embedding("hello")

    assert "Authorization" not in gateway.requests[0].headers


@pytest.mark.parametrize(("header", "expected"), [("X-Tenant-Id", "X-Tenant-Id"), ("X-Org", "X-Org")])
async def test_the_tenant_header_is_sent_only_when_a_tenant_is_set(monkeypatch, header, expected):
    monkeypatch.setattr(settings, "GATEWAY_TENANT_HEADER", header)
    monkeypatch.setattr(settings, "GATEWAY_TENANT", " acme ")
    gateway = FakeGateway()
    adapter, _ = build(gateway)

    await adapter.generate_embedding("hello")

    assert gateway.requests[0].headers[expected] == "acme"


# --------------------------------------------------------------------------
# Retry policy
# --------------------------------------------------------------------------
async def test_429_waits_the_retry_after_and_retries_the_same_chunk():
    gateway = FakeGateway(script=[error_response(429, "BACKEND_RATE_LIMITED", **{"Retry-After": "2"})])
    adapter, sleeps = build(gateway)

    assert await adapter.generate_embeddings(["hello"]) == [vector_for("hello")]

    assert sleeps.waits == [2.0]
    first, second = gateway.requests
    # A retry replays the same chunk: same idempotency key and correlation id, a fresh request id.
    assert first.headers["X-Idempotency-Key"] == second.headers["X-Idempotency-Key"]
    assert first.headers["X-Correlation-Id"] == second.headers["X-Correlation-Id"]
    assert gateway.bodies[0]["request_id"] != gateway.bodies[1]["request_id"]
    assert gateway.bodies[0]["input"] == gateway.bodies[1]["input"]


async def test_503_honours_a_retry_after_given_as_an_http_date():
    when = format_datetime(datetime.now(UTC) + timedelta(seconds=20), usegmt=True)
    gateway = FakeGateway(script=[error_response(503, "BACKEND_UNAVAILABLE", **{"Retry-After": when})])
    adapter, sleeps = build(gateway)

    await adapter.generate_embedding("hello")

    assert 15 < sleeps.waits[0] <= 20


async def test_an_http_date_in_the_past_means_retry_at_once():
    gateway = FakeGateway(script=[error_response(
        429, "BACKEND_RATE_LIMITED", **{"Retry-After": "Wed, 21 Oct 2015 07:28:00 GMT"},
    )])
    adapter, sleeps = build(gateway)

    await adapter.generate_embedding("hello")

    assert sleeps.waits == [0.0]


async def test_a_date_without_a_zone_is_taken_as_utc():
    when = (datetime.now(UTC) + timedelta(seconds=10)).strftime("%d %b %Y %H:%M:%S -0000")
    gateway = FakeGateway(script=[error_response(503, "BACKEND_UNAVAILABLE", **{"Retry-After": when})])
    adapter, sleeps = build(gateway)

    await adapter.generate_embedding("hello")

    assert 5 < sleeps.waits[0] <= 10


async def test_a_long_retry_after_is_shortened():
    gateway = FakeGateway(script=[error_response(429, "TENANT_QUOTA_EXCEEDED", **{"Retry-After": "3600"})])
    adapter, sleeps = build(gateway)

    await adapter.generate_embedding("hello")

    assert sleeps.waits == [30.0]


@pytest.mark.parametrize("retry_after", [None, "soon", "nan", "inf"])
async def test_without_a_usable_retry_after_the_wait_grows(retry_after):
    headers = {} if retry_after is None else {"Retry-After": retry_after}
    gateway = FakeGateway(script=[error_response(502, "BACKEND_UNAVAILABLE", **headers)] * 3)
    adapter, sleeps = build(gateway)

    await adapter.generate_embedding("hello")

    assert sleeps.waits == [0.5, 1.0, 2.0]


@pytest.mark.parametrize("status", [502, 503, 504])
async def test_a_persistent_5xx_is_retried_a_bounded_number_of_times_then_fails(status):
    gateway = FakeGateway(script=[error_response(status, "BACKEND_UNAVAILABLE")] * 10)
    adapter, sleeps = build(gateway)

    with pytest.raises(GatewayEmbeddingError, match=rf"failed after 4 attempts.*HTTP {status}, code BACKEND_UNAVAILABLE"):
        await adapter.generate_embedding("hello")

    assert len(gateway.requests) == 4
    assert len(sleeps.waits) == 3


async def test_the_number_of_attempts_can_be_lowered():
    gateway = FakeGateway(script=[error_response(503, "BACKEND_UNAVAILABLE")] * 10)
    adapter, _ = build(gateway, max_attempts=0)

    with pytest.raises(GatewayEmbeddingError, match="failed after 1 attempts"):
        await adapter.generate_embedding("hello")

    assert len(gateway.requests) == 1


@pytest.mark.parametrize(("status", "code"), [
    (403, "BUDGET_EXCEEDED"),
    (403, "MODEL_NOT_APPROVED"),
    (422, "EMBEDDINGS_UNSUPPORTED"),
])
async def test_403_and_422_are_never_retried(status, code):
    gateway = FakeGateway(script=[error_response(status, code)])
    adapter, sleeps = build(gateway)

    with pytest.raises(GatewayEmbeddingError, match=rf"refused the request: HTTP {status}, code {code}"):
        await adapter.generate_embedding("hello")

    assert len(gateway.requests) == 1
    assert sleeps.waits == []


@pytest.mark.parametrize("response", [
    httpx.Response(500, json={"code": "INFERENCE_TIMEOUT"}),
    httpx.Response(401, content=b"not json"),
    httpx.Response(403, json=["a", "list"]),
    httpx.Response(403, json={"code": "lower case and spaces <script>"}),
    httpx.Response(204),
])
async def test_any_other_status_fails_at_once_and_never_repeats_the_body(response):
    gateway = FakeGateway(script=[response])
    adapter, _ = build(gateway)

    with pytest.raises(GatewayEmbeddingError, match="refused the request") as failure:
        await adapter.generate_embedding("hello")

    assert "<script>" not in str(failure.value)
    assert len(gateway.requests) == 1


async def test_a_transport_error_is_retried_and_can_recover():
    gateway = FakeGateway(script=[httpx.ConnectError("connection refused"), httpx.ReadTimeout("slow")])
    adapter, sleeps = build(gateway)

    assert await adapter.generate_embedding("hello") == vector_for("hello")

    assert sleeps.waits == [0.5, 1.0]
    assert len({request.headers["X-Idempotency-Key"] for request in gateway.requests}) == 1


async def test_a_persistent_transport_error_fails_with_its_type():
    gateway = FakeGateway(script=[httpx.ConnectError("connection refused")] * 10)
    adapter, _ = build(gateway)

    with pytest.raises(GatewayEmbeddingError, match="failed after 4 attempts, last failure: ConnectError"):
        await adapter.generate_embedding("hello")


class RefusesAfterTheFirstRequest(FakeGateway):
    def __call__(self, request):
        if self.requests:
            self.requests.append(request)
            return error_response(403, "BUDGET_EXCEEDED")
        return super().__call__(request)


async def test_a_later_chunk_that_fails_fails_the_whole_call():
    gateway = RefusesAfterTheFirstRequest()
    adapter, _ = build(gateway)

    with pytest.raises(GatewayEmbeddingError, match="BUDGET_EXCEEDED"):
        await adapter.generate_embeddings(["x"] * 300)

    assert len(gateway.requests) == 2


# --------------------------------------------------------------------------
# Validation of what comes back
# --------------------------------------------------------------------------
async def test_a_vector_of_the_wrong_width_fails_with_a_clear_error():
    adapter, _ = build(FakeGateway(width=8))

    with pytest.raises(GatewayEmbeddingError, match=r"8-dimensional vectors but EMBEDDING_DIMENSIONS is 4"):
        await adapter.generate_embedding("hello")


@pytest.mark.parametrize("answer", [
    {"embeddings": [[1.0, 2.0, 3.0, 4.0]]},  # two texts sent, one vector back
    {"embeddings": []},
    {"embeddings": "nope"},
    {"embeddings": {"a": 1}},
])
async def test_a_wrong_number_of_vectors_is_refused(answer):
    adapter, _ = build(FakeGateway(script=[httpx.Response(200, json=answer)]))

    with pytest.raises(GatewayEmbeddingError, match="different number of vectors than the 2 texts"):
        await adapter.generate_embeddings(["a", "b"])


@pytest.mark.parametrize("response", [
    httpx.Response(200, content=b"<html>not json</html>"),
    httpx.Response(200, json=["not", "an", "object"]),
    httpx.Response(200, json={"vectors": []}),
])
async def test_an_answer_without_an_embeddings_list_is_refused(response):
    adapter, _ = build(FakeGateway(script=[response]))

    with pytest.raises(GatewayEmbeddingError, match="without an embeddings list"):
        await adapter.generate_embedding("hello")


@pytest.mark.parametrize("vector", [
    "1234",
    [1.0, 2.0, 3.0, "4"],
    [1.0, 2.0, 3.0, True],
    [1.0, 2.0, 3.0, None],
    [[1.0], 2.0, 3.0, 4.0],
])
async def test_a_malformed_vector_is_refused(vector):
    adapter, _ = build(FakeGateway(script=[httpx.Response(200, json={"embeddings": [vector]})]))

    with pytest.raises(GatewayEmbeddingError, match="malformed vector at position 0"):
        await adapter.generate_embedding("hello")


@pytest.mark.parametrize("literal", ["NaN", "Infinity", "-Infinity", "1" + "0" * 400])
async def test_a_non_finite_number_is_refused(literal):
    content = f'{{"embeddings": [[1.0, 2.0, 3.0, {literal}]]}}'.encode()
    adapter, _ = build(FakeGateway(script=[httpx.Response(200, content=content)]))

    with pytest.raises(GatewayEmbeddingError, match="non-finite number in the vector at position 0"):
        await adapter.generate_embedding("hello")


async def test_integers_in_a_vector_are_accepted_as_floats():
    answer = httpx.Response(200, json={"embeddings": [[1, 2, 3, 4]]})
    adapter, _ = build(FakeGateway(script=[answer]))

    vector = await adapter.generate_embedding("hello")

    assert vector == [1.0, 2.0, 3.0, 4.0]
    assert all(type(value) is float for value in vector)


# --------------------------------------------------------------------------
# Configuration is checked when the adapter is built
# --------------------------------------------------------------------------
def test_missing_settings_are_named(monkeypatch):
    monkeypatch.setattr(settings, "GATEWAY_URL", "")
    monkeypatch.setattr(settings, "GATEWAY_MODEL", "  ")

    with pytest.raises(ValueError, match="missing: GATEWAY_URL, GATEWAY_MODEL"):
        GatewayEmbeddingsAdapter()


@pytest.mark.parametrize("url", [
    "gateway.example.test",
    "ftp://gateway.example.test",
    "https://",
    "http://exa mple.com",
    "http://host:abc",
])
def test_the_url_must_be_http_with_a_host(monkeypatch, url):
    monkeypatch.setattr(settings, "GATEWAY_URL", url)

    with pytest.raises(ValueError, match="GATEWAY_URL"):
        GatewayEmbeddingsAdapter()


@pytest.mark.parametrize("url", [
    "https://user:pass@gateway.example.test",
    "https://gateway.example.test/?token=abc",
    "https://gateway.example.test/#frag",
])
def test_the_url_carries_no_credentials_query_or_fragment(monkeypatch, url):
    monkeypatch.setattr(settings, "GATEWAY_URL", url)

    with pytest.raises(ValueError, match="must not carry credentials") as failure:
        GatewayEmbeddingsAdapter()

    assert "pass" not in str(failure.value)
    assert "abc" not in str(failure.value)


@pytest.mark.parametrize("setting", ["GATEWAY_AUTH_HEADER", "GATEWAY_TENANT_HEADER"])
@pytest.mark.parametrize("name", ["", "Bad Header", "X-Evil\r\nInjected: 1", "Ünicode"])
def test_header_names_are_validated(monkeypatch, setting, name):
    monkeypatch.setattr(settings, setting, name)

    with pytest.raises(ValueError, match=f"{setting} must be a valid HTTP header name"):
        GatewayEmbeddingsAdapter()


@pytest.mark.parametrize(("setting", "value"), [
    ("GATEWAY_TOKEN", SecretStr("abc\r\nX-Injected: 1")),
    ("GATEWAY_TOKEN", SecretStr("tøken")),
    ("GATEWAY_TENANT", "acme\nx"),
])
def test_header_values_must_be_printable_ascii(monkeypatch, setting, value):
    monkeypatch.setattr(settings, setting, value)

    with pytest.raises(ValueError, match="printable ASCII") as failure:
        GatewayEmbeddingsAdapter()

    assert "Injected" not in str(failure.value)


# --------------------------------------------------------------------------
# Secrets stay out of logs, errors and URLs
# --------------------------------------------------------------------------
async def test_the_token_never_reaches_logs_urls_or_errors(caplog):
    caplog.set_level(logging.DEBUG)
    gateway = FakeGateway(script=[error_response(503, "BACKEND_UNAVAILABLE")] * 10)
    adapter, _ = build(gateway)

    with pytest.raises(GatewayEmbeddingError) as failure:
        await adapter.generate_embedding("hello")

    assert len(gateway.requests) == 4
    assert TOKEN not in caplog.text
    assert TOKEN not in str(failure.value)
    assert all(TOKEN not in str(request.url) for request in gateway.requests)
    assert TOKEN not in repr(settings.GATEWAY_TOKEN)
    retries = [record for record in caplog.records if record.getMessage().startswith("Embeddings gateway request failed")]
    assert len(retries) == 3
    assert retries[0].gateway_url == "https://gateway.example.test/api/v1/embeddings"


# --------------------------------------------------------------------------
# Settings and wiring: the provider is optional
# --------------------------------------------------------------------------
@pytest.fixture
def clean_environment(monkeypatch):
    """No GATEWAY_* or EMBEDDING_PROVIDER variable from the host, so defaults show."""
    names = [name for name in Settings.model_fields if name.startswith("GATEWAY_")]
    for name in [*names, "EMBEDDING_PROVIDER"]:
        monkeypatch.delenv(name, raising=False)


def test_the_default_provider_is_the_local_tensorrt_embedder_not_the_gateway(clean_environment):
    assert Settings(_env_file=None).EMBEDDING_PROVIDER == "TensorRT"


def test_the_default_provider_runs_without_any_gateway_setting_or_gateway_code(
    monkeypatch, clean_environment, tensorrt_embedder,
):
    """GATEWAY_* is read only by the gateway adapter, which the bootstrap does not even construct."""
    from app.bootstrap import get_embedding_adapter
    from app.repositories.embeddings import gateway_adapter as gateway_module

    class MustNotBeBuilt:
        def __init__(self, *args, **kwargs):
            raise AssertionError("the gateway adapter was built although EMBEDDING_PROVIDER is TensorRT")

    monkeypatch.setattr(gateway_module, "GatewayEmbeddingsAdapter", MustNotBeBuilt)
    for name in (name for name in Settings.model_fields if name.startswith("GATEWAY_")):
        monkeypatch.setattr(settings, name, Settings.model_fields[name].default)
    monkeypatch.setattr(settings, "EMBEDDING_PROVIDER", "TensorRT")
    monkeypatch.setattr(settings, "EMBEDDING_DIMENSIONS", 384)  # this module's fixture narrows it for the fake gateway

    assert get_embedding_adapter() is tensorrt_embedder


def test_selecting_the_gateway_without_its_settings_is_the_only_way_to_need_them(monkeypatch):
    from app.bootstrap import get_embedding_adapter

    for name in (name for name in Settings.model_fields if name.startswith("GATEWAY_")):
        monkeypatch.setattr(settings, name, Settings.model_fields[name].default)
    monkeypatch.setattr(settings, "EMBEDDING_PROVIDER", "gateway")

    with pytest.raises(ValueError, match="GATEWAY_URL"):
        get_embedding_adapter()


def test_gateway_settings_default_to_empty_and_nothing_requires_them(clean_environment):
    loaded = Settings(_env_file=None)

    assert (loaded.GATEWAY_URL, loaded.GATEWAY_BACKEND, loaded.GATEWAY_MODEL, loaded.GATEWAY_AGENT_ID) == ("",) * 4
    assert (loaded.GATEWAY_PATH, loaded.GATEWAY_AUTH_HEADER, loaded.GATEWAY_TENANT_HEADER) == (
        "/api/v1/embeddings", "Authorization", "X-Tenant-Id",
    )
    assert (loaded.GATEWAY_TENANT, loaded.GATEWAY_METADATA, loaded.GATEWAY_SUPPORTS_DIMENSIONS) == ("", {}, False)
    assert loaded.GATEWAY_TOKEN.get_secret_value() == ""


def test_gateway_settings_come_from_the_environment_and_the_token_stays_secret(monkeypatch):
    monkeypatch.setenv("EMBEDDING_PROVIDER", "GATEWAY")
    monkeypatch.setenv("GATEWAY_TOKEN", TOKEN)
    monkeypatch.setenv("GATEWAY_METADATA", '{"team": "memory"}')
    monkeypatch.setenv("GATEWAY_SUPPORTS_DIMENSIONS", "true")

    loaded = Settings(_env_file=None)

    assert loaded.EMBEDDING_PROVIDER == "gateway"
    assert loaded.GATEWAY_TOKEN.get_secret_value() == TOKEN
    assert loaded.GATEWAY_METADATA == {"team": "memory"}
    assert loaded.GATEWAY_SUPPORTS_DIMENSIONS is True
    assert TOKEN not in repr(loaded)
    assert TOKEN not in str(loaded.model_dump())


def test_gateway_metadata_values_must_be_strings(monkeypatch):
    monkeypatch.setenv("GATEWAY_METADATA", '{"retries": 3}')

    with pytest.raises(ValidationError, match="GATEWAY_METADATA"):
        Settings(_env_file=None)


def test_the_bootstrap_builds_the_gateway_adapter(monkeypatch):
    from app.bootstrap import get_embedding_adapter

    monkeypatch.setattr(settings, "EMBEDDING_PROVIDER", "gateway")

    assert isinstance(get_embedding_adapter(), GatewayEmbeddingsAdapter)


# --------------------------------------------------------------------------
# The re-embedding service sends one request per batch
# --------------------------------------------------------------------------
async def test_a_full_re_embed_sends_each_batch_of_memories_in_one_request():
    now = datetime.now(UTC)
    memories = [
        Memory(
            id=index, title=f"Memory {index}", content="content", context="ctx", keywords=["k"], tags=["t"],
            importance=7, created_at=now, updated_at=now,
        )
        for index in range(1, 26)
    ]
    repo = AsyncMock()
    repo.count_all_memories.return_value = len(memories)

    async def page(limit, offset):
        return memories[offset:offset + limit]

    repo.get_memories_for_reembedding.side_effect = page
    repo.validate_embedding_count.return_value = True
    repo.validate_embedding_dimensions.return_value = True
    repo.validate_search_works.return_value = True
    gateway = FakeGateway()
    adapter, _ = build(gateway)

    result = await ReEmbeddingService(repo, adapter, batch_size=10).re_embed_all()

    assert result.total_processed == 25
    assert [len(body["input"]) for body in gateway.bodies] == [10, 10, 5]
    updates = [update for call in repo.bulk_update_embeddings.await_args_list for update in call.args[0]]
    assert [memory_id for memory_id, _ in updates] == list(range(1, 26))
    assert all(len(vector) == WIDTH for _, vector in updates)


@pytest.mark.parametrize(("provider", "shown"), [
    ("gateway", "embed-small"),
    ("Azure", "azure-deployment"),
    ("OpenAI", "some/local-model"),
    ("TensorRT", "some/local-model"),
])
def test_the_re_embed_preview_names_the_model_the_provider_uses(monkeypatch, capsys, provider, shown):
    import main

    monkeypatch.setattr(settings, "EMBEDDING_PROVIDER", provider)
    monkeypatch.setattr(settings, "AZURE_DEPLOYMENT", "azure-deployment")
    monkeypatch.setattr(settings, "EMBEDDING_MODEL", "some/local-model")

    main._print_reembed_configuration(20)

    assert f"Provider: {provider} ({shown})" in capsys.readouterr().out
