"""Reranked search honours ``k`` against the real Qdrant repository.

The embedder is the real TensorRT embedder and the cross-encoder is the real TensorRT reranker
(behind a recorder that passes every call through), so these tests drive the real candidate
fetch, rerank hand-off and truncation in ``search_scored``.
"""
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from uuid import uuid4

import pytest

from app.config.settings import settings
from app.models.memory_models import MemoryCreate
from app.repositories.embeddings.reranker_adapter import HttpRerankAdapter


class RecordingReranker:
    """The real reranker, which also remembers how many documents it was given."""

    def __init__(self, real):
        self.real = real
        self.document_counts = []

    async def rerank(self, query, documents):
        self.document_counts.append(len(documents))
        return await self.real.rerank(query, documents)


@pytest.fixture
async def rerank_env(qdrant_stack, monkeypatch):
    monkeypatch.setattr(settings, "RERANKING_ENABLED", True)
    monkeypatch.setattr(settings, "MEMORY_NUM_AUTO_LINK", 0)
    return qdrant_stack, uuid4()


@pytest.fixture
def reranker(tensorrt_reranker):
    return RecordingReranker(tensorrt_reranker)


async def _memories(stack, user_id, reranker, count, embeddings):
    repo = stack.memory_repository(embeddings, reranker)
    for index in range(count):
        await repo.create_memory(user_id, MemoryCreate(
            title=f"memory {index}", content=f"content {index}", context="context",
            keywords=["k"], tags=["t"], importance=7,
        ))
    return repo


async def _search_memories(repo, user_id, k, query="q"):
    return await repo.search_scored(
        user_id=user_id, query=query, query_context="", k=k,
        importance_threshold=None, project=None, exclude_ids=None,
    )


async def test_memory_search_returns_k_when_k_exceeds_dense_candidates(
    rerank_env, monkeypatch, reranker, tensorrt_embedder,
):
    stack, user_id = rerank_env
    monkeypatch.setattr(settings, "DENSE_SEARCH_CANDIDATES", 5)
    repo = await _memories(stack, user_id, reranker, 12, tensorrt_embedder)

    found = await _search_memories(repo, user_id, k=8)

    assert len(found) == 8


async def test_memory_search_returns_every_match_when_fewer_than_k(
    rerank_env, monkeypatch, reranker, tensorrt_embedder,
):
    stack, user_id = rerank_env
    monkeypatch.setattr(settings, "DENSE_SEARCH_CANDIDATES", 5)
    repo = await _memories(stack, user_id, reranker, 6, tensorrt_embedder)

    found = await _search_memories(repo, user_id, k=8)

    assert len(found) == 6


async def test_memory_search_still_reranks_when_k_is_within_dense_candidates(
    rerank_env, monkeypatch, reranker, tensorrt_embedder,
):
    stack, user_id = rerank_env
    monkeypatch.setattr(settings, "DENSE_SEARCH_CANDIDATES", 5)
    repo = await _memories(stack, user_id, reranker, 8, tensorrt_embedder)

    found = await _search_memories(repo, user_id, k=3)

    assert reranker.document_counts == [5]
    assert len(found) == 3
    assert all(score.rerank_score is not None for _, score in found)


class _IndexOrderRerankHandler(BaseHTTPRequestHandler):
    """A /v1/rerank server that answers in document order instead of score order."""

    def do_POST(self):  # noqa: N802
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        results = [
            {"index": index, "relevance_score": 0.95 if "BESTMATCH" in document else 0.05}
            for index, document in enumerate(body["documents"])
        ]
        payload = json.dumps({"results": results}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *args):
        pass


@pytest.fixture
def index_order_rerank_url():
    server = HTTPServer(("127.0.0.1", 0), _IndexOrderRerankHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/v1/rerank"
    finally:
        server.shutdown()
        server.server_close()


async def test_http_reranker_answering_in_document_order_still_picks_best_match(
    rerank_env, monkeypatch, index_order_rerank_url, tensorrt_embedder,
):
    stack, user_id = rerank_env
    monkeypatch.setattr(settings, "DENSE_SEARCH_CANDIDATES", 20)
    repo = stack.memory_repository(
        tensorrt_embedder, HttpRerankAdapter(model="m", url=index_order_rerank_url, api_key=""),
    )
    for word in ("alpha", "beta", "BESTMATCH"):
        await repo.create_memory(user_id, MemoryCreate(
            title=f"{word} memory", content=f"content about {word}", context="context",
            keywords=[word], tags=["t"], importance=7,
        ))

    # The dense search ranks alpha first for this query, so the best match is not the first document
    # the reranker receives: only a reranker that sorts by score picks it.
    with monkeypatch.context() as unranked:
        unranked.setattr(settings, "RERANKING_ENABLED", False)
        dense = await stack.memory_repository(tensorrt_embedder).search_scored(
            user_id, "alpha", "", 3, None, None, None,
        )
    assert [memory.keywords for memory, _ in dense][0] == ["alpha"]
    assert "BESTMATCH" in [memory.keywords[0] for memory, _ in dense]

    found = await _search_memories(repo, user_id, k=1, query="alpha")

    assert len(found) == 1
    memory, score = found[0]
    assert "BESTMATCH" in memory.title
    assert score.rerank_score == pytest.approx(0.95)
