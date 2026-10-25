"""Integration tests for the conventions behind the one-line service failures (app/repositories/service_failures.py)

The lines name a server by the address its request went to, so two spellings of one address must compare equal (the
default port is filled in), and the tables that name each server and provider say with an empty string that a server
takes no credential or that a provider has no address setting. The lines that follow from the empty strings are checked
as an operator reads them.
"""
import httpx
import pytest

from app.config.settings import settings
from app.repositories.service_failures import (
    _origin,
    _Provider,
    _provider_of,
    _services,
    describe_provider_failure,
)


class _SdkError(Exception):
    """An error as the SDK of a provider raises it, told apart only by the module its class comes from."""


def _error_from(package: str, **attributes) -> Exception:
    kind = type("APIError", (_SdkError,), {"__module__": f"{package}.errors"})
    error = kind("a message that must not be repeated sk-secret")
    for name, value in attributes.items():
        setattr(error, name, value)
    return error


@pytest.mark.parametrize(
    ("address", "origin"),
    [
        ("http://qdrant.test", ("http", "qdrant.test", 80)),
        ("https://qdrant.test", ("https", "qdrant.test", 443)),
        ("http://qdrant.test:80", ("http", "qdrant.test", 80)),
        ("https://qdrant.test:443/collections", ("https", "qdrant.test", 443)),
        ("http://qdrant.test:6333", ("http", "qdrant.test", 6333)),
        ("https://qdrant.test:8443/path?x=1", ("https", "qdrant.test", 8443)),
        ("HTTP://QDRANT.TEST", ("http", "qdrant.test", 80)),
        ("ftp://files.test", ("ftp", "files.test", 0)),
        ("ws://socket.test", ("ws", "socket.test", 0)),
        (httpx.URL("https://typed.test"), ("https", "typed.test", 443)),
    ],
)
def test_an_address_is_its_scheme_host_and_port_with_the_default_port_filled_in(address, origin):
    assert _origin(address) == origin


@pytest.mark.parametrize("address", ["", "http://", "/relative/path", "qdrant.test:6333", "http://[::1", "http://:6333"])
def test_an_address_that_names_no_host_has_no_origin(address):
    assert _origin(address) is None


def test_each_server_that_can_fail_is_named_with_the_setting_of_its_credential_and_what_to_check():
    assert [(service.name, service.credential_setting, service.reach) for service in _services()] == [
        ("Qdrant", "QDRANT_API_KEY", "QDRANT_URL and that the node runs"),
        ("The embeddings gateway", "GATEWAY_TOKEN", "GATEWAY_URL and that the gateway runs"),
        ("The rerank service", "RERANKING_API_KEY", "RERANKING_URL and that the service runs"),
        (
            "The token introspection endpoint",
            "FASTMCP_SERVER_AUTH_INTROSPECTION_CLIENT_SECRET",
            "FASTMCP_SERVER_AUTH_INTROSPECTION_URL and that the endpoint runs",
        ),
        ("Ollama", "", "OLLAMA_BASE_URL and that Ollama runs"),
        ("Google", "GOOGLE_AI_API_KEY", "the network access to Google"),
    ]


def test_each_provider_says_with_an_empty_string_that_it_has_no_key_or_no_address_setting(monkeypatch):
    assert _provider_of("openai") == _Provider("OpenAI", "OPENAI_API_KEY", "OPENAI_BASE_URL", "EMBEDDING_MODEL")
    assert _provider_of("ollama") == _Provider("Ollama", "", "OLLAMA_BASE_URL", "EMBEDDING_MODEL")
    assert _provider_of("google") == _Provider("Google", "GOOGLE_AI_API_KEY", "", "EMBEDDING_MODEL")

    monkeypatch.setattr(settings, "EMBEDDING_PROVIDER", " Azure ")
    assert _provider_of("openai") == _Provider("Azure OpenAI", "AZURE_API_KEY", "AZURE_ENDPOINT", "AZURE_DEPLOYMENT")


def test_a_provider_without_an_address_setting_sends_the_operator_to_the_network():
    google = _provider_of("google")

    assert google.check_address() == "the network access to the provider"
    assert _provider_of("ollama").check_address() == "OLLAMA_BASE_URL"


def test_an_unreachable_provider_without_an_address_setting_says_to_check_the_network():
    line = describe_provider_failure(_error_from("google"))

    assert line == "Google cannot be reached or did not answer in time (APIError); check the network access to the provider"


def test_a_provider_without_a_key_that_refuses_sends_the_operator_to_its_address():
    assert describe_provider_failure(_error_from("ollama", status_code=401)) == (
        "Ollama refused the request (HTTP 401): check OLLAMA_BASE_URL"
    )
    assert describe_provider_failure(_error_from("google", code=403)) == (
        "Google refused the request (HTTP 403): check GOOGLE_AI_API_KEY"
    )


def test_a_provider_without_an_address_setting_that_does_not_know_the_model_names_the_model_only():
    assert describe_provider_failure(_error_from("google", code=404)) == (
        "Google does not know the model or the address (HTTP 404): check EMBEDDING_MODEL"
    )
    assert describe_provider_failure(_error_from("ollama", status_code=404)) == (
        "Ollama does not know the model or the address (HTTP 404): check EMBEDDING_MODEL and OLLAMA_BASE_URL"
    )
