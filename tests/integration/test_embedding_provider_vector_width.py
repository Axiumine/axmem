# SPDX-FileCopyrightText: 2026 Giovanni Manzoni
# SPDX-License-Identifier: AGPL-3.0-only

"""The remote embeddings providers answer with the width of their model, which must be EMBEDDING_DIMENSIONS.

Each adapter runs on its real client library against an in-process server (``FakeEmbeddingProvider``), so the
vector travels the way a provider's answer does. A vector of another width would be refused by Qdrant (or worse,
searched against a collection of another width), so the adapter says so first, naming the provider and both widths.
"""
import pytest

from app.config.settings import settings
from app.exceptions import EmbeddingUnavailableError
from tests.embedding_provider_server import (
    PROVIDERS,
    FakeEmbeddingProvider,
    adapter_for,
)

WIDTH = 4


@pytest.fixture(autouse=True)
def configured_width(monkeypatch):
    monkeypatch.setattr(settings, "EMBEDDING_DIMENSIONS", WIDTH)


@pytest.mark.parametrize("provider", PROVIDERS)
async def test_a_vector_of_the_configured_width_is_returned_as_floats(provider, monkeypatch):
    with FakeEmbeddingProvider([0.5, -0.25, 1, 0]) as server:
        adapter = adapter_for(provider, server, monkeypatch)

        single = await adapter.generate_embedding("one text")
        batch = await adapter.generate_embeddings(["first", "second"])

    assert single == [0.5, -0.25, 1.0, 0.0]
    assert all(type(value) is float for value in single)
    assert batch == [single, single]
    assert len(server.requests) == 3  # one request per text


@pytest.mark.parametrize(("provider", "name"), [
    ("Azure", "Azure OpenAI"), ("Google", "Google"), ("OpenAI", "OpenAI"), ("Ollama", "Ollama"),
])
@pytest.mark.parametrize("returned", [WIDTH - 1, WIDTH + 4, 1536])
async def test_a_vector_of_another_width_is_refused_naming_the_provider_and_both_widths(provider, name, returned, monkeypatch):
    with FakeEmbeddingProvider([0.25] * returned) as server:
        adapter = adapter_for(provider, server, monkeypatch)

        with pytest.raises(EmbeddingUnavailableError) as raised:
            await adapter.generate_embedding("one text")

    message = str(raised.value)
    assert message.startswith(f"{name} returned {returned}-dimensional embeddings but EMBEDDING_DIMENSIONS is {WIDTH}.")
    assert f"Set EMBEDDING_DIMENSIONS={returned}" in message
    assert "--re-embed" in message


@pytest.mark.parametrize("provider", PROVIDERS)
async def test_a_batch_stops_at_the_first_vector_of_the_wrong_width(provider, monkeypatch):
    with FakeEmbeddingProvider([0.25] * 8) as server:
        adapter = adapter_for(provider, server, monkeypatch)

        with pytest.raises(EmbeddingUnavailableError, match="8-dimensional"):
            await adapter.generate_embeddings(["first", "second", "third"])

    assert len(server.requests) == 1  # the others would only repeat the refusal


@pytest.mark.parametrize("provider", PROVIDERS)
@pytest.mark.parametrize("bad", [float("nan"), float("inf")], ids=["nan", "infinity"])
async def test_a_vector_with_a_number_that_is_not_finite_is_refused(provider, bad, monkeypatch):
    with FakeEmbeddingProvider([0.5, bad, 0.5, 0.5]) as server:
        adapter = adapter_for(provider, server, monkeypatch)

        with pytest.raises(RuntimeError, match="returned a non-finite number in the embedding") as raised:
            await adapter.generate_embedding("one text")

    assert not isinstance(raised.value, EmbeddingUnavailableError)  # one text's vector: the others may be fine
