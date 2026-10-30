# SPDX-FileCopyrightText: 2026 Giovanni Manzoni
# SPDX-License-Identifier: AGPL-3.0-only

"""Embedding adapters: missing configuration and provider answers that carry no vector.

The provider SDK objects are replaced by small in-process fakes, so nothing touches the network
and no model is downloaded.
"""
from types import SimpleNamespace

import pytest

from app.config.settings import settings
from app.repositories.embeddings import embedding_adapter as ea

AZURE_CONFIG = {
    "AZURE_ENDPOINT": "https://my-resource.openai.azure.com/",
    "AZURE_API_KEY": "test-key",
    "AZURE_API_VERSION": "2023-05-15",
    "AZURE_DEPLOYMENT": "text-embedding-ada-002",
}


@pytest.fixture
def azure_adapter(monkeypatch):
    for name, value in AZURE_CONFIG.items():
        monkeypatch.setattr(settings, name, value)
    return ea.AzureOpenAIAdapter()


@pytest.fixture
def google_adapter(monkeypatch):
    monkeypatch.setattr(settings, "GOOGLE_AI_API_KEY", "test-key")
    return ea.GoogleEmbeddingsAdapter()


def _google_answering(adapter, embeddings):
    """Make the adapter's Google client answer every request with the given ``embeddings``."""
    async def embed_content(**_request):
        return SimpleNamespace(embeddings=embeddings)

    adapter.client = SimpleNamespace(aio=SimpleNamespace(models=SimpleNamespace(embed_content=embed_content)))


async def test_azure_answer_without_a_response_object_is_reported(azure_adapter):
    azure_adapter.client = SimpleNamespace(embeddings=SimpleNamespace(create=lambda **_request: None))

    with pytest.raises(RuntimeError, match=r"^Azure response did not contain embedding vector$"):
        await azure_adapter.generate_embedding("hello")


async def test_azure_answer_returns_the_first_embedding(azure_adapter, monkeypatch):
    monkeypatch.setattr(settings, "EMBEDDING_DIMENSIONS", 2)
    answer = SimpleNamespace(data=[SimpleNamespace(embedding=[0.25, 0.5])])
    azure_adapter.client = SimpleNamespace(embeddings=SimpleNamespace(create=lambda **_request: answer))

    assert await azure_adapter.generate_embedding("hello") == [0.25, 0.5]


def test_google_adapter_requires_an_api_key(monkeypatch):
    monkeypatch.setattr(settings, "GOOGLE_AI_API_KEY", "")

    with pytest.raises(ValueError, match=r"^GOOGLE_AI_API_KEY must be configured for GoogleEmbeddingsAdapter$"):
        ea.GoogleEmbeddingsAdapter()


async def test_google_embedding_values_are_returned_as_floats(google_adapter, monkeypatch):
    monkeypatch.setattr(settings, "EMBEDDING_DIMENSIONS", 2)
    _google_answering(google_adapter, [SimpleNamespace(values=[1, 2.5])])

    result = await google_adapter.generate_embedding("hello")

    assert result == [1.0, 2.5]
    assert all(isinstance(value, float) for value in result)


@pytest.mark.parametrize(
    ("embeddings", "error", "message"),
    [
        (None, TypeError, "'NoneType' object is not subscriptable"),
        ([SimpleNamespace(values=None)], TypeError, "'NoneType' object is not iterable"),
        ([], IndexError, "list index out of range"),
    ],
    ids=["no-embeddings-list", "no-values", "empty-embeddings-list"],
)
async def test_google_answer_without_a_vector_raises_the_error_a_plain_lookup_would(
    google_adapter, embeddings, error, message,
):
    _google_answering(google_adapter, embeddings)

    with pytest.raises(error, match=f"^{message}$"):
        await google_adapter.generate_embedding("hello")
