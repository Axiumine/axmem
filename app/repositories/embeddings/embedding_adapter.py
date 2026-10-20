"""Embedding Adapter to abstract provider specific implementation details"""
import asyncio
import logging
from typing import NotRequired, TypedDict

from google import genai
from google.genai import types
from openai import AzureOpenAI, OpenAI

from app.config.settings import settings
from app.protocols.embedding_protocol import EmbeddingsAdapter
from app.utils.url_helpers import redact_url

logger = logging.getLogger(__name__)


class _EmbeddingsCreateKwargs(TypedDict):
    """Keyword arguments for ``client.embeddings.create`` on the OpenAI-style clients."""

    input: list[str]
    model: str
    dimensions: NotRequired[int]  # only sent to deployments that accept it


class _OpenAIClientKwargs(TypedDict):
    """Keyword arguments for the ``OpenAI`` client constructor."""

    api_key: str
    timeout: float
    base_url: NotRequired[str]  # left out entirely to keep the library default


class _OneTextPerRequest(EmbeddingsAdapter):
    """Batch entry point for providers whose client embeds one text per request.

    ``generate_embeddings`` calls ``generate_embedding`` for each text in turn, so it keeps
    that method's logging, error handling and rate-limit behaviour.
    """

    async def generate_embeddings(self, texts: list[str]) -> list[list[float]]:
        return [await self.generate_embedding(text) for text in texts]


class AzureOpenAIAdapter(_OneTextPerRequest):
    """Generate embeddings using Azure Open AI Embeddings Provider"""

    def __init__(self):
        logger.info("Initialising Azure Open AI embeddings adapter", extra={
            "embedding_model": settings.EMBEDDING_MODEL,
            "supports_dimensions": settings.AZURE_SUPPORTS_DIMENSIONS,
        })

        required = {
            "AZURE_ENDPOINT": settings.AZURE_ENDPOINT,
            "AZURE_API_KEY": settings.AZURE_API_KEY,
            "AZURE_API_VERSION": settings.AZURE_API_VERSION,
            "AZURE_DEPLOYMENT": settings.AZURE_DEPLOYMENT,
        }
        missing = [name for name, value in required.items() if not value.strip()]
        if missing:
            raise ValueError(
                "AZURE_ENDPOINT, AZURE_API_KEY, AZURE_API_VERSION and AZURE_DEPLOYMENT "
                f"must be configured for AzureOpenAIAdapter; missing: {', '.join(missing)}",
            )

        self.client = AzureOpenAI(
            api_version=settings.AZURE_API_VERSION,
            azure_endpoint=settings.AZURE_ENDPOINT,
            api_key=settings.AZURE_API_KEY,
            timeout=settings.EMBEDDING_REQUEST_TIMEOUT_SECONDS,
        )
        self.model = settings.AZURE_DEPLOYMENT
        self.supports_dimensions = settings.AZURE_SUPPORTS_DIMENSIONS

    async def generate_embedding(self, text: str) -> list[float]:
        kwargs: _EmbeddingsCreateKwargs = {
            "input": [text],
            "model": self.model,
        }
        if self.supports_dimensions:
            kwargs["dimensions"] = settings.EMBEDDING_DIMENSIONS

        try:
            response = await asyncio.to_thread(
                self.client.embeddings.create,
                **kwargs,
            )
        except Exception:
            logger.error("Error generatring embeddings", exc_info=True, extra={
                "embedding provider": "Azure",
                "embedding model": self.model,
            })
            raise

        if response is None:
            raise RuntimeError("Azure response did not contain embedding vector")

        embeddings  = response.data[0].embedding

        return embeddings

class GoogleEmbeddingsAdapter(_OneTextPerRequest):
    """Generate embeddings using Google Generative AI embedding models."""

    def __init__(self):
        logger.info("Initialising Google Generative AI embeddings adapter", extra={
            "embedding_model": settings.EMBEDDING_MODEL,
        })

        api_key = settings.GOOGLE_AI_API_KEY
        self.model = settings.EMBEDDING_MODEL

        if not api_key:
            raise ValueError("GOOGLE_AI_API_KEY must be configured for GoogleEmbeddingsAdapter")

        self.client = genai.Client(
            api_key=api_key,
            http_options=types.HttpOptions(
                timeout=max(1, round(settings.EMBEDDING_REQUEST_TIMEOUT_SECONDS * 1000)),  # milliseconds
            ),
        )

    async def generate_embedding(self, text: str) -> list[float]:
        try:
            response = await self.client.aio.models.embed_content(
                model=self.model,
                contents=text,
                config=types.EmbedContentConfig(
                    output_dimensionality=settings.EMBEDDING_DIMENSIONS,
                ),
            )
        except Exception:
            logger.error("Error generating embeddings", exc_info=True, extra={
                "embedding_provider": "google",
                "embedding_model": self.model,
            })
            raise

        # Keep the exceptions the unguarded expression raised: TypeError for a missing
        # embeddings list or values, IndexError for an empty list.
        embeddings = response.embeddings
        if embeddings is None:
            raise TypeError("'NoneType' object is not subscriptable")
        values = embeddings[0].values
        if values is None:
            raise TypeError("'NoneType' object is not iterable")
        return list(map(float, values))


def _openai_api_key(base_url: str | None) -> str:
    """API key for the OpenAI client.

    Endpoints that need no key (local OpenAI-compatible servers) still require a non-empty
    value, so a placeholder is used when a custom base URL is configured.
    """
    api_key = settings.OPENAI_API_KEY
    if api_key:
        return api_key
    if base_url:
        return "no-key-required"
    raise ValueError("OPENAI_API_KEY must be configured for OpenAIEmbeddingsAdapter")


class OpenAIEmbeddingsAdapter(_OneTextPerRequest):
    """Generate embeddings using OpenAI's embedding models.

    Supports custom base_url for OpenAI-compatible endpoints (llama.cpp, vLLM, LiteLLM).
    When base_url is set and no API key is provided, a placeholder key is used.
    Set OPENAI_SUPPORTS_DIMENSIONS=false for endpoints that don't support the dimensions param.
    """

    def __init__(self):
        base_url: str | None = settings.OPENAI_BASE_URL or None
        self.supports_dimensions = settings.OPENAI_SUPPORTS_DIMENSIONS

        logger.info("Initialising OpenAI embeddings adapter", extra={
            "embedding_model": settings.EMBEDDING_MODEL,
            "base_url": redact_url(base_url) if base_url else "(default)",
            "supports_dimensions": self.supports_dimensions,
        })

        api_key = _openai_api_key(base_url)

        client_kwargs: _OpenAIClientKwargs = {
            "api_key": api_key,
            "timeout": settings.EMBEDDING_REQUEST_TIMEOUT_SECONDS,
        }
        if base_url:
            client_kwargs["base_url"] = base_url

        self.client = OpenAI(**client_kwargs)
        self.model = settings.EMBEDDING_MODEL

    async def generate_embedding(self, text: str) -> list[float]:
        kwargs: _EmbeddingsCreateKwargs = {
            "input": [text],
            "model": self.model,
        }
        if self.supports_dimensions:
            kwargs["dimensions"] = settings.EMBEDDING_DIMENSIONS

        try:
            response = await asyncio.to_thread(
                self.client.embeddings.create,
                **kwargs,
            )
        except Exception:
            logger.error("Error generating embeddings", exc_info=True, extra={
                "embedding_provider": "openai",
                "embedding_model": self.model,
            })
            raise

        if response is None:
            raise RuntimeError("OpenAI response did not contain embedding vector")

        return response.data[0].embedding


class OllamaEmbeddingsAdapter(_OneTextPerRequest):
    """Generate embeddings using Ollama's native API."""

    def __init__(self):
        from ollama import AsyncClient

        logger.info("Initialising Ollama embeddings adapter", extra={
            "embedding_model": settings.EMBEDDING_MODEL,
            "base_url": redact_url(settings.OLLAMA_BASE_URL),
        })
        self.client = AsyncClient(
            host=settings.OLLAMA_BASE_URL,
            timeout=settings.EMBEDDING_REQUEST_TIMEOUT_SECONDS,
        )
        self.model = settings.EMBEDDING_MODEL

    async def generate_embedding(self, text: str) -> list[float]:
        try:
            response = await self.client.embed(
                model=self.model,
                input=text,
                dimensions=settings.EMBEDDING_DIMENSIONS,
            )
        except Exception:
            logger.error("Error generating embeddings", exc_info=True, extra={
                "embedding_provider": "ollama",
                "embedding_model": self.model,
            })
            raise

        if not response or not response.embeddings:
            raise RuntimeError("Ollama did not return embedding vector")

        return list(response.embeddings[0])
