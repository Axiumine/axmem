# SPDX-FileCopyrightText: 2026 Giovanni Manzoni
# SPDX-License-Identifier: AGPL-3.0-only

"""The HTTP reranker adapter, word for word: what a missing URL says and what a request carries.

The adapter runs on a real ``httpx`` client whose transport is an in-process server, so the request is what leaves the
client: its header names, its body and its timeout.
"""
import functools
import json

import httpx
import pytest

from app.config.settings import settings
from app.repositories.embeddings import reranker_adapter
from app.repositories.embeddings.reranker_adapter import HttpRerankAdapter

URL = "http://reranker.internal:8080/v1/rerank"


class RerankServer:
    """Answers every request with the same three results, neither in score nor in index order, and keeps the requests."""

    def __init__(self):
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return httpx.Response(200, json={"results": [
            {"index": 0, "relevance_score": 0.5},
            {"index": 2, "relevance_score": 0.9},
            {"index": 1, "relevance_score": 0.1},
        ]})


@pytest.fixture
def server(monkeypatch):
    rerank_server = RerankServer()
    monkeypatch.setattr(
        reranker_adapter.httpx, "AsyncClient",
        functools.partial(httpx.AsyncClient, transport=httpx.MockTransport(rerank_server)),
    )
    return rerank_server


@pytest.fixture(autouse=True)
def reranking_settings(monkeypatch):
    monkeypatch.setattr(settings, "RERANKING_MODEL", "settings-model")
    monkeypatch.setattr(settings, "RERANKING_URL", "http://settings.invalid/rerank")
    monkeypatch.setattr(settings, "RERANKING_API_KEY", "settings-key")
    monkeypatch.setattr(settings, "RERANKING_TIMEOUT", 7.5)


def test_without_a_url_the_start_is_refused_with_an_example():
    with pytest.raises(ValueError) as refused:
        HttpRerankAdapter(url="  ")

    assert str(refused.value) == (
        "RERANKING_URL must be set when RERANKING_PROVIDER=HTTP (e.g. http://localhost:8080/v1/rerank)."
    )


def test_every_setting_is_the_default_of_its_argument():
    adapter = HttpRerankAdapter()

    assert (adapter.model, adapter.url, adapter.api_key, adapter.timeout) == (
        "settings-model", "http://settings.invalid/rerank", "settings-key", 7.5,
    )


def test_an_argument_wins_over_its_setting_even_when_it_is_empty_or_zero():
    adapter = HttpRerankAdapter(model="", url=URL, api_key="", timeout=0.0)

    assert (adapter.model, adapter.url, adapter.api_key, adapter.timeout) == ("", URL, "", 0.0)


async def test_a_request_carries_the_key_the_body_and_the_timeout(server):
    adapter = HttpRerankAdapter(url=URL, timeout=3.0)

    ranked = await adapter.rerank("what is AI?", ["cooking", "AI", "weather"])

    assert ranked == [(2, 0.9), (0, 0.5), (1, 0.1)]
    (request,) = server.requests
    assert request.method == "POST"
    assert str(request.url) == URL
    assert dict(request.headers.raw)[b"Authorization"] == b"Bearer settings-key"
    assert json.loads(request.content) == {
        "query": "what is AI?", "documents": ["cooking", "AI", "weather"], "model": "settings-model",
    }
    assert request.extensions["timeout"] == {"connect": 3.0, "read": 3.0, "write": 3.0, "pool": 3.0}


async def test_the_configured_timeout_is_the_default_of_a_request(server):
    await HttpRerankAdapter(url=URL).rerank("query", ["a", "b"])

    assert server.requests[0].extensions["timeout"] == {"connect": 7.5, "read": 7.5, "write": 7.5, "pool": 7.5}


async def test_without_a_key_no_authorization_is_sent(server):
    await HttpRerankAdapter(url=URL, api_key="").rerank("query", ["a", "b"])

    assert b"Authorization" not in dict(server.requests[0].headers.raw)
