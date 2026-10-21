"""One line that says which service failed and what to check, for the failures of the services Axmem talks to.

``operator_failure`` (``app/bootstrap.py``) turns an error that an operator can fix into the line a terminal shows.
Qdrant's own client errors are described next to the client (``describe_failure``); this module covers the rest:

- an ``httpx`` error, named after the server the request went to and not always Qdrant. The snapshot download talks
  to a Qdrant node, the embeddings adapter to the gateway, the rerank adapter to the rerank service and the
  introspection verifier to the introspection endpoint, and each of them can leave an ``httpx`` error uncaught;
- the error of a remote embeddings SDK (OpenAI and Azure OpenAI, Google, Ollama), named after the provider.

No message repeats the text of the error or of the URL (a key or a password can be part of either): the status, the
name of the error class, the host and the names of the settings to check are all that is said.
"""
import os
from dataclasses import dataclass

import httpx

from app.config.settings import settings

# Where the Google SDK sends its requests unless ``GOOGLE_GEMINI_BASE_URL`` (a variable the SDK reads) says otherwise
GOOGLE_API_URL = "https://generativelanguage.googleapis.com"
_HTTP_STATUS_REFUSED = (401, 403)
_DEFAULT_PORTS = {"http": 80, "https": 443}


@dataclass(frozen=True)
class _Service:
    """A server that Axmem sends HTTP requests to, and where its address and credential are configured."""

    name: str  # how a message begins
    urls: tuple[str, ...]
    credential_setting: str  # empty when the server takes no credential
    reach: str  # what to check when it does not answer


def _services() -> tuple[_Service, ...]:
    return (
        _Service("Qdrant", tuple(settings.qdrant_urls), "QDRANT_API_KEY", "QDRANT_URL and that the node runs"),
        _Service(
            "The embeddings gateway", (settings.GATEWAY_URL,), "GATEWAY_TOKEN", "GATEWAY_URL and that the gateway runs",
        ),
        _Service(
            "The rerank service", (settings.RERANKING_URL,), "RERANKING_API_KEY", "RERANKING_URL and that the service runs",
        ),
        _Service(
            "The token introspection endpoint",
            (settings.FASTMCP_SERVER_AUTH_INTROSPECTION_URL,),
            "FASTMCP_SERVER_AUTH_INTROSPECTION_CLIENT_SECRET",
            "FASTMCP_SERVER_AUTH_INTROSPECTION_URL and that the endpoint runs",
        ),
        # The SDKs of Ollama and Google leave a timeout or a refused connection as a plain httpx error
        _Service("Ollama", (settings.OLLAMA_BASE_URL,), "", "OLLAMA_BASE_URL and that Ollama runs"),
        _Service(
            "Google",
            (os.environ.get("GOOGLE_GEMINI_BASE_URL") or GOOGLE_API_URL,),
            "GOOGLE_AI_API_KEY",
            "the network access to Google",
        ),
    )


def _origin(url: str | httpx.URL) -> tuple[str, str, int] | None:
    """Scheme, host and port of a URL, with the default port filled in; None when it names no host."""
    try:
        parsed = httpx.URL(url)
    except httpx.InvalidURL:
        return None
    if not parsed.host:
        return None
    return parsed.scheme, parsed.host, parsed.port or _DEFAULT_PORTS.get(parsed.scheme, 0)


def _request_url(exc: httpx.HTTPError) -> httpx.URL | None:
    """The URL the failed request went to; a transport error that was built without a request has none."""
    try:
        return exc.request.url
    except RuntimeError:  # httpx: "The .request property has not been set."
        return None


def _service_of(url: httpx.URL | None) -> _Service | None:
    origin = _origin(url) if url is not None else None
    if origin is None:
        return None
    return next((service for service in _services() if origin in map(_origin, service.urls)), None)


def describe_http_failure(exc: httpx.HTTPError) -> str:
    """Why an HTTP call failed, naming the server it went to (Qdrant, the gateway, ...) by the address of the request."""
    url = _request_url(exc)
    service = _service_of(url)
    if service is None:
        # Not an address the settings name: say which host it was, and nothing that would have to be guessed
        name = f"The server at {url.host}" if url is not None and url.host else "A server"
        credential, reach = "", ""
    else:
        name = service.name
        credential = f": check {service.credential_setting}" if service.credential_setting else ""
        reach = f"; check {service.reach}"
    if isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code
        if status in _HTTP_STATUS_REFUSED:
            return f"{name} refused the request (HTTP {status}){credential}"
        return f"{name} answered HTTP {status} to {exc.request.method} {exc.request.url.path}"
    return f"{name} cannot be reached or did not answer in time ({type(exc).__name__}){reach}"


@dataclass(frozen=True)
class _Provider:
    """A remote embeddings provider as its SDK errors show it."""

    name: str
    credential_setting: str  # empty when the provider takes no key
    endpoint_setting: str  # where its address is configured; empty when Axmem has no such setting (Google)
    model_setting: str

    def check_address(self) -> str:
        return self.endpoint_setting or "the network access to the provider"


def _provider_of(sdk: str) -> _Provider:
    if sdk == "openai":
        if str(settings.EMBEDDING_PROVIDER).strip().lower() == "azure":
            return _Provider("Azure OpenAI", "AZURE_API_KEY", "AZURE_ENDPOINT", "AZURE_DEPLOYMENT")
        return _Provider("OpenAI", "OPENAI_API_KEY", "OPENAI_BASE_URL", "EMBEDDING_MODEL")
    if sdk == "ollama":
        return _Provider("Ollama", "", "OLLAMA_BASE_URL", "EMBEDDING_MODEL")
    return _Provider("Google", "GOOGLE_AI_API_KEY", "", "EMBEDDING_MODEL")


# Top-level packages whose errors are the SDK errors of a provider; the classes are recognised by the module they
# come from, so that describing an error never imports an SDK that the configuration does not use
_SDKS = frozenset({"openai", "ollama", "google"})


def _sdk_of(exc: BaseException) -> str | None:
    for kind in type(exc).__mro__:
        package = kind.__module__.partition(".")[0]
        if package in _SDKS:
            return package
    return None


def _status_of(exc: BaseException) -> int | None:
    """The HTTP status an SDK error carries: ``status_code`` (OpenAI, Ollama) or ``code`` (Google)."""
    for attribute in ("status_code", "code"):
        value = getattr(exc, attribute, None)
        if isinstance(value, int) and not isinstance(value, bool):
            return value
    return None


def describe_provider_failure(exc: BaseException) -> str | None:
    """Why a remote embeddings provider failed, naming it; None when ``exc`` is not an error of one of the SDKs."""
    sdk = _sdk_of(exc)
    if sdk is None:
        return None
    provider = _provider_of(sdk)
    status = _status_of(exc)
    if status is None or status < httpx.codes.BAD_REQUEST:
        return (
            f"{provider.name} cannot be reached or did not answer in time ({type(exc).__name__}); "
            f"check {provider.check_address()}"
        )
    if status in _HTTP_STATUS_REFUSED:
        check = provider.credential_setting or provider.check_address()
        return f"{provider.name} refused the request (HTTP {status}): check {check}"
    if status == httpx.codes.NOT_FOUND:
        where = f" and {provider.endpoint_setting}" if provider.endpoint_setting else ""
        return f"{provider.name} does not know the model or the address (HTTP {status}): check {provider.model_setting}{where}"
    if status == httpx.codes.TOO_MANY_REQUESTS:
        return f"{provider.name} is limiting the requests or has no quota left (HTTP {status})"
    if status >= httpx.codes.INTERNAL_SERVER_ERROR:
        return f"{provider.name} failed on its side (HTTP {status})"
    return f"{provider.name} refused the request (HTTP {status})"


def describe_service_failure(exc: BaseException) -> str | None:
    """The line for an ``httpx`` error or the error of a provider's SDK; None for anything else."""
    if isinstance(exc, httpx.HTTPError):
        return describe_http_failure(exc)
    return describe_provider_failure(exc)
