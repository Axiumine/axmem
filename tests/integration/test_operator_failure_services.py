# SPDX-FileCopyrightText: 2026 Giovanni Manzoni
# SPDX-License-Identifier: AGPL-3.0-only

"""One line for a failed remote embeddings SDK or HTTP call, naming what it was talking to; a lifespan that fails ends the same way.

``operator_failure`` once knew Qdrant only: the error of the OpenAI, Azure OpenAI, Google or Ollama SDK during a
re-embedding or an import ended as a traceback, and every plain ``httpx`` error was called a Qdrant error, whichever
server it came from. The SDK errors here are the real ones: each adapter runs on its real client library against an
in-process server (``FakeEmbeddingProvider``) that answers with an error status, or against a port nobody listens on.
"""
import subprocess
import sys
import textwrap
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

import main
from app.bootstrap import operator_failure
from app.config.settings import settings
from app.exceptions import QdrantStartupError
from app.repositories.service_failures import (
    describe_http_failure,
    describe_provider_failure,
    describe_service_failure,
)
from app.routes.cli import operator_commands
from tests.embedding_provider_server import (
    ERROR_TEXT,
    PROVIDERS,
    FakeEmbeddingProvider,
    adapter_for,
)

ROOT = Path(__file__).resolve().parents[2]
VECTOR = [0.5] * 384


@pytest.fixture(autouse=True)
def configured_provider(request, monkeypatch):
    """The provider the settings select, which is what tells OpenAI from Azure OpenAI (one SDK serves both)."""
    provider = request.node.callspec.params.get("provider") if hasattr(request.node, "callspec") else None
    if provider:
        monkeypatch.setattr(settings, "EMBEDDING_PROVIDER", provider)


def _no_retries(adapter):
    """The OpenAI SDK retries a 429, a 5xx and a refused connection with a pause; the test wants the first error."""
    if hasattr(adapter.client, "with_options"):
        adapter.client = adapter.client.with_options(max_retries=0)
    return adapter


async def _failure_of(provider, monkeypatch, status):
    with FakeEmbeddingProvider(VECTOR, status=status) as server:
        adapter = _no_retries(adapter_for(provider, server, monkeypatch))
        with pytest.raises(Exception) as raised:  # each SDK has its own error classes; that is the point
            await adapter.generate_embedding("a text")
    return raised.value


CREDENTIAL = {
    "Azure": "Azure OpenAI refused the request (HTTP {status}): check AZURE_API_KEY",
    "Google": "Google refused the request (HTTP {status}): check GOOGLE_AI_API_KEY",
    "OpenAI": "OpenAI refused the request (HTTP {status}): check OPENAI_API_KEY",
    "Ollama": "Ollama refused the request (HTTP {status}): check OLLAMA_BASE_URL",
}
MODEL = {
    "Azure": "Azure OpenAI does not know the model or the address (HTTP 404): check AZURE_DEPLOYMENT and AZURE_ENDPOINT",
    "Google": "Google does not know the model or the address (HTTP 404): check EMBEDDING_MODEL",
    "OpenAI": "OpenAI does not know the model or the address (HTTP 404): check EMBEDDING_MODEL and OPENAI_BASE_URL",
    "Ollama": "Ollama does not know the model or the address (HTTP 404): check EMBEDDING_MODEL and OLLAMA_BASE_URL",
}
NAMES = {"Azure": "Azure OpenAI", "Google": "Google", "OpenAI": "OpenAI", "Ollama": "Ollama"}


@pytest.mark.parametrize("provider", PROVIDERS)
@pytest.mark.parametrize("status", [401, 403])
async def test_a_provider_that_refuses_the_credentials_is_named_with_the_setting_to_check(provider, status, monkeypatch):
    error = await _failure_of(provider, monkeypatch, status)

    assert operator_failure(error) == CREDENTIAL[provider].format(status=status)


@pytest.mark.parametrize("provider", PROVIDERS)
async def test_a_provider_that_does_not_know_the_model_is_named(provider, monkeypatch):
    error = await _failure_of(provider, monkeypatch, 404)

    assert operator_failure(error) == MODEL[provider]


@pytest.mark.parametrize("provider", PROVIDERS)
@pytest.mark.parametrize(
    ("status", "line"),
    [
        (429, "{name} is limiting the requests or has no quota left (HTTP 429)"),
        (500, "{name} failed on its side (HTTP 500)"),
        (400, "{name} refused the request (HTTP 400)"),
    ],
)
async def test_a_provider_that_limits_fails_or_refuses_is_named_with_the_status(provider, status, line, monkeypatch):
    error = await _failure_of(provider, monkeypatch, status)

    assert operator_failure(error) == line.format(name=NAMES[provider])


@pytest.mark.parametrize("provider", PROVIDERS)
async def test_a_provider_that_cannot_be_reached_is_named(provider, monkeypatch):
    with FakeEmbeddingProvider(VECTOR) as server:
        adapter = _no_retries(adapter_for(provider, server, monkeypatch))
    # the server is gone: nothing listens on its port any more
    with pytest.raises(Exception) as raised:  # the class differs by SDK
        await adapter.generate_embedding("a text")

    line = operator_failure(raised.value)

    assert line is not None
    assert "\n" not in line
    assert NAMES[provider].split()[0] in line  # OpenAI and Azure OpenAI share an SDK; Ollama's own message names Ollama
    assert "127.0.0.1" not in line  # the address of a request is for the log, the setting to check is for the operator


@pytest.mark.parametrize("provider", PROVIDERS)
async def test_a_message_never_repeats_what_the_provider_said_or_the_key(provider, monkeypatch):
    error = await _failure_of(provider, monkeypatch, 401)

    line = operator_failure(error)

    assert line is not None
    assert ("sk-leaked-key" in ERROR_TEXT and ERROR_TEXT in str(error)) or provider == "Ollama"  # the SDK did carry it
    assert "provider-side-text" not in line
    assert "sk-" not in line
    assert "test-key" not in line


def test_an_error_that_is_not_an_sdk_error_is_left_to_the_caller():
    assert describe_provider_failure(KeyError("bug")) is None
    assert describe_service_failure(KeyError("bug")) is None


class _SdkError(Exception):
    """An error as the SDK of a provider raises it, told apart only by the module its class comes from."""


def _error_from(package: str, **attributes) -> Exception:
    kind = type("APIError", (_SdkError,), {"__module__": f"{package}.errors"})
    error = kind("a message that must not be repeated sk-secret")
    for name, value in attributes.items():
        setattr(error, name, value)
    return error


def test_an_sdk_error_without_a_status_is_a_connection_failure_naming_the_class():
    line = describe_provider_failure(_error_from("openai"))

    assert line == "OpenAI cannot be reached or did not answer in time (APIError); check OPENAI_BASE_URL"


def test_azure_is_named_when_the_settings_select_it(monkeypatch):
    monkeypatch.setattr(settings, "EMBEDDING_PROVIDER", "azure")

    assert describe_provider_failure(_error_from("openai", status_code=429)) == (
        "Azure OpenAI is limiting the requests or has no quota left (HTTP 429)"
    )


def test_the_status_of_a_google_error_is_its_code():
    assert describe_provider_failure(_error_from("google", code=503)) == "Google failed on its side (HTTP 503)"
    assert describe_provider_failure(_error_from("google", code=True)).startswith("Google cannot be reached")  # a bool is no status


def test_a_status_below_400_means_the_answer_was_unusable_not_refused():
    assert describe_provider_failure(_error_from("ollama", status_code=200)).startswith("Ollama cannot be reached")


# ---- httpx errors are named after the server the request went to -------------------------------------------------


@pytest.fixture
def addresses(monkeypatch):
    monkeypatch.setattr(settings, "QDRANT_URL", "http://qdrant-a.test:6333,https://qdrant-b.test")
    monkeypatch.setattr(settings, "GATEWAY_URL", "https://gateway.test")
    monkeypatch.setattr(settings, "RERANKING_URL", "http://rerank.test:8080/v1/rerank")
    monkeypatch.setattr(settings, "FASTMCP_SERVER_AUTH_INTROSPECTION_URL", "https://auth.test/oauth/introspect")
    monkeypatch.setattr(settings, "OLLAMA_BASE_URL", "http://ollama.test:11434")
    monkeypatch.delenv("GOOGLE_GEMINI_BASE_URL", raising=False)


def _status_error(url: str, status: int, method: str = "POST") -> httpx.HTTPStatusError:
    request = httpx.Request(method, url)
    return httpx.HTTPStatusError("failed", request=request, response=httpx.Response(status, request=request))


def _transport_error(url: str) -> httpx.ConnectTimeout:
    error = httpx.ConnectTimeout("slow")
    error.request = httpx.Request("POST", url)
    return error


SERVERS = [
    ("http://qdrant-a.test:6333/collections/axmem_links/snapshots/s1", "Qdrant", "QDRANT_API_KEY", "QDRANT_URL and that the node runs"),
    ("https://qdrant-b.test:443/collections", "Qdrant", "QDRANT_API_KEY", "QDRANT_URL and that the node runs"),
    ("https://gateway.test/api/v1/embeddings", "The embeddings gateway", "GATEWAY_TOKEN", "GATEWAY_URL and that the gateway runs"),
    ("http://rerank.test:8080/v1/rerank", "The rerank service", "RERANKING_API_KEY", "RERANKING_URL and that the service runs"),
    (
        "https://auth.test/oauth/introspect",
        "The token introspection endpoint",
        "FASTMCP_SERVER_AUTH_INTROSPECTION_CLIENT_SECRET",
        "FASTMCP_SERVER_AUTH_INTROSPECTION_URL and that the endpoint runs",
    ),
    ("http://ollama.test:11434/api/embed", "Ollama", None, "OLLAMA_BASE_URL and that Ollama runs"),
    (
        "https://generativelanguage.googleapis.com/v1beta/models/m:embedContent",
        "Google",
        "GOOGLE_AI_API_KEY",
        "the network access to Google",
    ),
]


@pytest.mark.usefixtures("addresses")
@pytest.mark.parametrize(("url", "name", "credential", "reach"), SERVERS)
def test_an_http_error_is_named_after_the_server_it_went_to(url, name, credential, reach):
    path = httpx.URL(url).path

    assert describe_http_failure(_transport_error(url)) == (
        f"{name} cannot be reached or did not answer in time (ConnectTimeout); check {reach}"
    )
    assert describe_http_failure(_status_error(url, 500)) == f"{name} answered HTTP 500 to POST {path}"
    refused = describe_http_failure(_status_error(url, 403))
    assert refused == f"{name} refused the request (HTTP 403)" + (f": check {credential}" if credential else "")
    assert operator_failure(_status_error(url, 401)) == describe_http_failure(_status_error(url, 401))


@pytest.mark.usefixtures("addresses")
def test_an_http_error_is_not_always_a_qdrant_error():
    lines = {operator_failure(_transport_error(url)).split(" cannot be reached")[0] for url, *_ in SERVERS}

    assert lines == {"Qdrant", "The embeddings gateway", "The rerank service", "The token introspection endpoint", "Ollama", "Google"}


@pytest.mark.usefixtures("addresses")
def test_a_google_endpoint_set_by_the_sdk_variable_is_still_google(monkeypatch):
    monkeypatch.setenv("GOOGLE_GEMINI_BASE_URL", "http://google-proxy.test:9000")

    assert describe_http_failure(_transport_error("http://google-proxy.test:9000/v1beta/x")).startswith("Google cannot be reached")


@pytest.mark.usefixtures("addresses")
def test_an_address_that_no_setting_names_is_reported_by_its_host_and_nothing_is_guessed():
    other = "https://user:secret@elsewhere.test:8443/private/path?token=abc"

    assert describe_http_failure(_transport_error(other)) == (
        "The server at elsewhere.test cannot be reached or did not answer in time (ConnectTimeout)"
    )
    assert describe_http_failure(_status_error(other, 403)) == "The server at elsewhere.test refused the request (HTTP 403)"
    for line in (describe_http_failure(_transport_error(other)), describe_http_failure(_status_error(other, 500))):
        assert "secret" not in line
        assert "abc" not in line


@pytest.mark.usefixtures("addresses")
def test_an_error_built_without_a_request_names_no_server():
    assert describe_http_failure(httpx.ReadTimeout("slow")) == "A server cannot be reached or did not answer in time (ReadTimeout)"


def test_a_setting_that_is_empty_or_not_a_url_never_matches_a_request(monkeypatch):
    monkeypatch.setattr(settings, "GATEWAY_URL", "")
    monkeypatch.setattr(settings, "RERANKING_URL", "not a url at all")

    assert describe_http_failure(_transport_error("http://host.test/x")) == (
        "The server at host.test cannot be reached or did not answer in time (ConnectTimeout)"
    )


# ---- a command that talks to a provider ends in a line -----------------------------------------------------------


@pytest.fixture
def reembed_run(monkeypatch):
    import app.bootstrap
    import app.config.logging_config

    class Stack:
        closed = False

        async def close(self):
            self.closed = True

    stack = Stack()
    monkeypatch.setattr(app.config.logging_config, "configure_logging", lambda **_: None)
    monkeypatch.setattr(main.atexit, "register", lambda *_: None)
    monkeypatch.setattr(app.bootstrap, "create_qdrant_stack", lambda: stack)
    return stack, app.bootstrap


@pytest.mark.parametrize("provider", PROVIDERS)
async def test_a_re_embedding_whose_provider_refuses_ends_with_a_line_naming_it(provider, reembed_run, monkeypatch, capsys):
    stack, bootstrap = reembed_run
    with FakeEmbeddingProvider(VECTOR, status=401) as server:
        adapter = _no_retries(adapter_for(provider, server, monkeypatch))
        monkeypatch.setattr(bootstrap, "get_embedding_adapter", lambda: adapter)

        async def embed(_stack, embedder, _args):
            await embedder.generate_embeddings(["a memory"])

        monkeypatch.setattr(main, "_reembed", embed)
        with pytest.raises(SystemExit) as exit_info:
            await main._run_reembed(SimpleNamespace(batch_size=5, dry_run=False))

    error = capsys.readouterr().err
    assert exit_info.value.code == 1
    assert f"\n  ERROR: {CREDENTIAL[provider].format(status=401)}\n" in error
    assert "Traceback" not in error
    assert stack.closed


@pytest.mark.usefixtures("qdrant_env")
@pytest.mark.parametrize("provider", ["OpenAI", "Ollama"])
async def test_an_operator_command_whose_provider_fails_ends_with_a_line(provider, monkeypatch, capsys):
    """``import`` and the other commands share ``_with_service``, which asks ``operator_failure``."""
    with FakeEmbeddingProvider(VECTOR, status=500) as server:
        adapter = _no_retries(adapter_for(provider, server, monkeypatch))

        async def run(_service) -> int:
            await adapter.generate_embedding("a memory")
            return 0

        code = await operator_commands._with_service(run)

    assert code == 1
    # the adapter logs the failed call with its traceback, as it does inside the server; the command adds the line
    assert f"Error: {NAMES[provider]} failed on its side (HTTP 500)" in capsys.readouterr().err.splitlines()


# ---- a lifespan that fails after the pre-flight ends the same way -----------------------------------------------


async def test_a_lifespan_that_cannot_build_the_runtime_keeps_the_one_line_cause(monkeypatch):
    async def lost_node():
        raise QdrantStartupError("Qdrant at http://x:6333 cannot be reached; check QDRANT_URL")

    monkeypatch.setattr(main, "build_runtime", lost_node)
    monkeypatch.setattr(main, "_startup_refusal", None)
    monkeypatch.setattr(main.atexit, "register", lambda *_: None)

    with pytest.raises(main.StartupRefusedError) as raised:
        async with main.lifespan(main.mcp):
            pytest.fail("the lifespan started")

    assert str(raised.value) == "Qdrant at http://x:6333 cannot be reached; check QDRANT_URL"
    assert main._startup_refusal == str(raised.value)
    assert isinstance(raised.value.__cause__, QdrantStartupError)


async def test_a_bug_in_the_lifespan_keeps_its_traceback_and_is_no_refusal(monkeypatch):
    async def bug():
        raise KeyError("a bug")

    monkeypatch.setattr(main, "build_runtime", bug)
    monkeypatch.setattr(main, "_startup_refusal", None)
    monkeypatch.setattr(main.atexit, "register", lambda *_: None)

    with pytest.raises(KeyError, match="a bug"):
        async with main.lifespan(main.mcp):
            pytest.fail("the lifespan started")

    assert main._startup_refusal is None


@pytest.mark.parametrize(("refused", "sent"), [("a cause", ""), (None, "Traceback (most recent call last): ...")])
async def test_uvicorn_is_not_given_the_traceback_of_a_refusal_but_of_a_bug(monkeypatch, refused, sent):
    monkeypatch.setattr(main, "_startup_refusal", refused)
    seen = []

    async def app(scope, receive, send):
        await send({"type": "lifespan.startup.failed", "message": "Traceback (most recent call last): ..."})

    async def send(message):
        seen.append(message)

    await main._refusals_without_traceback(app)({"type": "lifespan"}, None, send)

    assert seen == [{"type": "lifespan.startup.failed", "message": sent}]


async def test_requests_and_other_lifespan_messages_pass_through_untouched(monkeypatch):
    monkeypatch.setattr(main, "_startup_refusal", "a cause")
    seen = []

    async def app(scope, receive, send):
        await send({"type": scope["type"] + ".message", "message": "kept"})
        if scope["type"] == "lifespan":
            await send({"type": "lifespan.startup.complete"})

    async def send(message):
        seen.append(message)

    await main._refusals_without_traceback(app)({"type": "http"}, None, send)
    await main._refusals_without_traceback(app)({"type": "lifespan"}, None, send)

    assert seen == [
        {"type": "http.message", "message": "kept"},
        {"type": "lifespan.message", "message": "kept"},
        {"type": "lifespan.startup.complete"},
    ]


SERVE_AFTER_THE_PREFLIGHT = textwrap.dedent(
    """
    import sys
    import main
    from app.exceptions import QdrantStartupError

    async def lost_node():
        # the pre-flight found Qdrant; the node went away before the lifespan built the runtime
        raise QdrantStartupError("Qdrant at http://x:6333 cannot be reached; check QDRANT_URL")

    main.build_runtime = lost_node
    main._start_refusal = lambda: None
    sys.exit(main._serve(sys.argv[1], "127.0.0.1", 8199))
    """,
)


@pytest.mark.parametrize("transport", ["http", "stdio"])
def test_a_race_after_the_preflight_ends_the_server_with_the_one_line_refusal(transport, tmp_path):
    result = subprocess.run(  # noqa: S603 - this interpreter and a script written above
        [sys.executable, "-c", SERVE_AFTER_THE_PREFLIGHT, transport],
        cwd=ROOT,
        env={"PATH": "/usr/bin:/bin", "PYTHONPATH": str(ROOT), "AUTH_MODE": "none", "XDG_CONFIG_HOME": str(tmp_path)},
        stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=120, check=False,
    )

    refusal = "Error: Qdrant at http://x:6333 cannot be reached; check QDRANT_URL"
    assert result.returncode == 1, result.stderr
    assert result.stderr.count(refusal) == 1
    assert "Traceback" not in result.stderr
    assert "Exception in 'lifespan' protocol" not in result.stderr
    assert result.stderr.rstrip().endswith(refusal)


# ---- what `_serve` does with a server that ended after the lifespan refused ----------------------------------------


@pytest.fixture
def serve_without_pre_flight(monkeypatch):
    """``_serve`` past its pre-flight, with a fake server in place of uvicorn; the fake decides how the server ends."""
    import contextlib

    import app.config.logging_config

    monkeypatch.setattr(app.config.logging_config, "logging_while_starting", lambda *_: contextlib.nullcontext())
    monkeypatch.setattr(main, "_configuration_refusal", lambda *_: None)
    monkeypatch.setattr(main, "_start_refusal", lambda: None)
    monkeypatch.setattr(main, "_startup_refusal", None)  # restored afterwards: ``_serve`` and the fakes assign it

    def serve(ends):
        monkeypatch.setattr(main, "_run_server", ends)
        return main._serve("http", "127.0.0.1", 8199)

    return serve


@pytest.mark.parametrize("failure", [SystemExit(3), RuntimeError("the server stopped"), ExceptionGroup("tasks", [OSError("x")])])
def test_a_server_that_ends_after_the_lifespan_refused_prints_the_kept_cause_and_returns_1(serve_without_pre_flight, capsys, failure):
    def ends(*_):
        main._startup_refusal = "Qdrant at http://x:6333 cannot be reached; check QDRANT_URL"  # what the lifespan keeps
        raise failure

    assert serve_without_pre_flight(ends) == 1

    assert capsys.readouterr().err == "Error: Qdrant at http://x:6333 cannot be reached; check QDRANT_URL\n"
    assert main._kept_startup_refusal() == "Qdrant at http://x:6333 cannot be reached; check QDRANT_URL"


def test_a_server_that_ends_with_an_error_and_no_refusal_keeps_the_error(serve_without_pre_flight, capsys):
    def ends(*_):
        raise RuntimeError("a bug of the server")

    with pytest.raises(RuntimeError, match="a bug of the server"):
        serve_without_pre_flight(ends)

    assert capsys.readouterr().err == ""
    assert main._kept_startup_refusal() is None


def test_a_server_that_stops_normally_returns_nothing(serve_without_pre_flight):
    assert serve_without_pre_flight(lambda *_: None) is None


def test_the_cause_kept_by_an_earlier_server_does_not_survive_into_the_next_serve(serve_without_pre_flight, monkeypatch):
    monkeypatch.setattr(main, "_startup_refusal", "left over")

    assert serve_without_pre_flight(lambda *_: None) is None
    assert main._kept_startup_refusal() is None


# ---- an address in the settings that is not a URL names no service -----------------------------------------------


def test_an_invalid_address_in_the_settings_names_no_service_and_does_not_hide_the_failing_host(monkeypatch):
    monkeypatch.setattr(settings, "GATEWAY_URL", "http://[::1")
    failure = httpx.ConnectError("refused", request=httpx.Request("GET", "http://other.example:9000/embed"))

    text = describe_http_failure(failure)

    assert "other.example" in text
    assert "GATEWAY_URL" not in text
