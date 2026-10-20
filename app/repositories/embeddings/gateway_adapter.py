"""Embeddings from an optional HTTP embeddings gateway (``EMBEDDING_PROVIDER=gateway``).

The adapter posts batches of texts as JSON to ``GATEWAY_URL`` + ``GATEWAY_PATH`` and checks what
comes back before any vector reaches the database: the count, the width and the numbers.
Every credential, header name and body field comes from settings.
"""
import asyncio
import json
import logging
import math
import re
import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from itertools import batched

import httpx

from app.config.settings import settings
from app.exceptions import EmbeddingUnavailableError
from app.protocols.embedding_protocol import EMBEDDING_BATCH_LIMIT, EmbeddingsAdapter
from app.utils.url_helpers import redact_url

logger = logging.getLogger(__name__)

# Statuses worth another attempt: rate limited, bad gateway, unavailable, gateway timeout.
# Anything else that is not 200 (403 and 422 included) fails at once.
_RETRYABLE_STATUSES = frozenset({429, 502, 503, 504})
# Refusals that concern the call as a whole (credentials, budget, model approval, address), not one of its texts
_REFUSING_STATUSES = frozenset({401, 403, 404})
_MAX_ATTEMPTS = 4
_BACKOFF_BASE_SECONDS = 0.5
# A longer wait (a large Retry-After) is shortened to this, so one request cannot park a re-embed.
_MAX_WAIT_SECONDS = 30.0

_HEADER_NAME = re.compile(r"[!#$%&'*+.^_`|~0-9A-Za-z-]+")
_ERROR_CODE = re.compile(r"[A-Z][A-Z0-9_]{0,63}")
_REQUIRED_SETTINGS = ("GATEWAY_URL", "GATEWAY_BACKEND", "GATEWAY_MODEL", "GATEWAY_AGENT_ID")


class GatewayEmbeddingError(RuntimeError):
    """The gateway refused a request, stayed unavailable, or answered with unusable vectors."""


class GatewayUnavailableError(GatewayEmbeddingError, EmbeddingUnavailableError):
    """A gateway failure that no single text caused: no credential or model, retries used up, a reply of the wrong shape."""


def _retry_after_seconds(response: httpx.Response) -> float | None:
    """Seconds the gateway asks us to wait, from a delay or an HTTP date; None if absent or unusable."""
    value = response.headers.get("Retry-After", "").strip()
    if not value:
        return None
    try:
        seconds = float(value)
    except ValueError:
        try:
            when = parsedate_to_datetime(value)
        except (TypeError, ValueError):
            return None
        seconds = (when.replace(tzinfo=when.tzinfo or UTC) - datetime.now(UTC)).total_seconds()
    return max(0.0, seconds) if math.isfinite(seconds) else None


def _error_code(response: httpx.Response) -> str:
    """The ``code`` of an error body when it looks like a code; the body itself is never repeated."""
    try:
        code = response.json().get("code")
    except (ValueError, AttributeError):
        return "unknown"
    return code if isinstance(code, str) and _ERROR_CODE.fullmatch(code) else "unknown"


def _finite_floats(raw: list[int | float]) -> list[float] | None:
    """The numbers as floats, or None when one is NaN, infinite or too large for a float."""
    try:
        vector = [float(value) for value in raw]
    except OverflowError:
        return None
    return vector if all(math.isfinite(value) for value in vector) else None


def _vector(raw: object, index: int, width: int) -> list[float]:
    """One returned vector as floats, refusing the wrong shape, width or non-finite numbers."""
    if not isinstance(raw, list) or any(type(value) not in (int, float) for value in raw):
        raise GatewayEmbeddingError(f"Embeddings gateway returned a malformed vector at position {index}")
    if len(raw) != width:
        raise GatewayUnavailableError(
            f"Embeddings gateway returned {len(raw)}-dimensional vectors but EMBEDDING_DIMENSIONS is {width}. "
            "Set EMBEDDING_DIMENSIONS to the width the gateway's model produces (or GATEWAY_SUPPORTS_DIMENSIONS=true "
            "if it can produce the configured width), then run --re-embed.",
        )
    vector = _finite_floats(raw)
    if vector is None:
        raise GatewayEmbeddingError(f"Embeddings gateway returned a non-finite number in the vector at position {index}")
    return vector


class GatewayEmbeddingsAdapter(EmbeddingsAdapter):
    """Generate embeddings through an HTTP embeddings gateway.

    ``transport`` and ``sleep`` exist so tests can run the adapter against an in-process gateway
    without waiting; production uses the defaults.
    """

    def __init__(
        self,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        max_attempts: int = _MAX_ATTEMPTS,
    ):
        missing = [name for name in _REQUIRED_SETTINGS if not getattr(settings, name).strip()]
        if missing:
            raise ValueError(
                "GATEWAY_URL, GATEWAY_BACKEND, GATEWAY_MODEL and GATEWAY_AGENT_ID must be configured "
                f"for GatewayEmbeddingsAdapter; missing: {', '.join(missing)}",
            )

        self.endpoint = self._endpoint(settings.GATEWAY_URL, settings.GATEWAY_PATH)
        self._headers = self._credential_headers()
        self.backend = settings.GATEWAY_BACKEND.strip()
        self.model = settings.GATEWAY_MODEL.strip()
        self.agent_id = settings.GATEWAY_AGENT_ID.strip()
        self.metadata = self._metadata(settings.GATEWAY_METADATA)
        self.supports_dimensions = settings.GATEWAY_SUPPORTS_DIMENSIONS
        self._transport = transport
        self._sleep = sleep
        self._max_attempts = max(1, max_attempts)

        logger.info("Initialising embeddings gateway adapter", extra={
            "gateway_url": redact_url(self.endpoint),
            "gateway_backend": self.backend,
            "gateway_model": self.model,
            "supports_dimensions": self.supports_dimensions,
        })

    @staticmethod
    def _metadata(raw: str) -> dict[str, str]:
        """The metadata object of ``GATEWAY_METADATA``; a blank value means none. Settings load it as plain text so
        that a bad value stops only the gateway provider, not a server that uses another one."""
        if not raw.strip():
            return {}
        try:
            value = json.loads(raw)
        except ValueError:
            value = None
        if not isinstance(value, dict) or not all(isinstance(item, str) for item in value.values()):
            raise ValueError("GATEWAY_METADATA must be a JSON object of string values")
        return value

    @staticmethod
    def _endpoint(base: str, path: str) -> str:
        try:
            url = httpx.URL(base.strip())
        except httpx.InvalidURL:
            raise ValueError("GATEWAY_URL is not a valid URL") from None
        if url.scheme not in ("http", "https") or not url.host or "%" in url.host:
            raise ValueError("GATEWAY_URL must be an http:// or https:// URL with a host")
        # An empty "?" or "#" has no query or fragment to see, but the path would be appended after it
        if url.userinfo or url.query or url.fragment or "?" in base or "#" in base:
            raise ValueError(
                "GATEWAY_URL must not carry credentials, a query or a fragment: "
                "set GATEWAY_TOKEN and GATEWAY_PATH instead",
            )
        return f"{str(url).rstrip('/')}/{path.strip().lstrip('/')}"

    @staticmethod
    def _credential_headers() -> dict[str, str]:
        """Authentication and tenant headers; the token never goes anywhere else."""
        headers: dict[str, str] = {}
        auth_name = settings.GATEWAY_AUTH_HEADER.strip()
        tenant_name = settings.GATEWAY_TENANT_HEADER.strip()
        tenant = settings.GATEWAY_TENANT.strip()
        token = settings.GATEWAY_TOKEN.get_secret_value().strip()
        for setting, name in (("GATEWAY_AUTH_HEADER", auth_name), ("GATEWAY_TENANT_HEADER", tenant_name)):
            if not _HEADER_NAME.fullmatch(name):
                raise ValueError(f"{setting} must be a valid HTTP header name")
        if not all(value.isascii() and value.isprintable() for value in (token, tenant)):
            raise ValueError("GATEWAY_TOKEN and GATEWAY_TENANT must be printable ASCII")
        if token:
            headers[auth_name] = f"Bearer {token}" if auth_name.lower() == "authorization" else token
        if tenant:
            headers[tenant_name] = tenant
        return headers

    def _body(self, texts: list[str]) -> dict[str, object]:
        body: dict[str, object] = {
            "request_id": str(uuid.uuid4()),
            "agent_id": self.agent_id,
            "backend": self.backend,
            "model": self.model,
            "input": texts,
            "metadata": self.metadata,
        }
        if self.supports_dimensions:
            body["dimensions"] = settings.EMBEDDING_DIMENSIONS
        return body

    async def generate_embedding(self, text: str) -> list[float]:
        return (await self.generate_embeddings([text]))[0]

    async def generate_embeddings(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []

        correlation_id = str(uuid.uuid4())
        vectors: list[list[float]] = []
        async with httpx.AsyncClient(
            transport=self._transport,
            timeout=settings.EMBEDDING_REQUEST_TIMEOUT_SECONDS,
        ) as client:
            for group in batched(texts, EMBEDDING_BATCH_LIMIT):
                vectors.extend(await self._embed_chunk(client, list(group), correlation_id))
        return vectors

    async def _embed_chunk(self, client: httpx.AsyncClient, texts: list[str], correlation_id: str) -> list[list[float]]:
        """One chunk of at most 256 texts; its retries share one idempotency key."""
        headers = {
            **self._headers,
            "X-Idempotency-Key": str(uuid.uuid4()),
            "X-Correlation-Id": correlation_id,
        }
        attempt = 0
        while True:
            attempt += 1
            wait: float | None = None
            try:
                response = await client.post(self.endpoint, headers=headers, json=self._body(texts))
            except httpx.TransportError as exc:
                failure = type(exc).__name__
            else:
                if response.status_code == httpx.codes.OK:
                    return self._vectors(response, len(texts))
                failure = f"HTTP {response.status_code}, code {_error_code(response)}"
                if response.status_code not in _RETRYABLE_STATUSES:
                    refused = GatewayUnavailableError if response.status_code in _REFUSING_STATUSES else GatewayEmbeddingError
                    raise refused(f"Embeddings gateway refused the request: {failure}")
                wait = _retry_after_seconds(response)
            if attempt >= self._max_attempts:
                raise GatewayUnavailableError(
                    f"Embeddings gateway failed after {attempt} attempts, last failure: {failure}",
                )
            delay = min(_BACKOFF_BASE_SECONDS * 2 ** (attempt - 1) if wait is None else wait, _MAX_WAIT_SECONDS)
            logger.warning("Embeddings gateway request failed, retrying", extra={
                "failure": failure,
                "attempt": attempt,
                "retry_in_seconds": delay,
                "gateway_url": redact_url(self.endpoint),
            })
            await self._sleep(delay)

    @staticmethod
    def _vectors(response: httpx.Response, expected: int) -> list[list[float]]:
        try:
            embeddings = response.json()["embeddings"]
        except (ValueError, KeyError, TypeError):
            raise GatewayEmbeddingError("Embeddings gateway answered without an embeddings list") from None
        if not isinstance(embeddings, list) or len(embeddings) != expected:
            raise GatewayUnavailableError(
                f"Embeddings gateway returned a different number of vectors than the {expected} texts sent",
            )
        width = settings.EMBEDDING_DIMENSIONS
        return [_vector(raw, index, width) for index, raw in enumerate(embeddings)]
