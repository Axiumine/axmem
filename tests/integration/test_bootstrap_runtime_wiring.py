# SPDX-FileCopyrightText: 2026 Giovanni Manzoni
# SPDX-License-Identifier: AGPL-3.0-only

"""Integration tests for what a runtime built by the composition root holds and says (app/bootstrap.py)

``build_runtime`` wires one repository, on the connections of one stack, to the real TensorRT models, hands the same
repository to the two services, and works out which tools the scope ceiling permits. It says each step at INFO. The
tests build a runtime on the test Qdrant node and read it back.
"""
import logging
from dataclasses import replace
from types import SimpleNamespace

import pytest

from app import bootstrap
from app.bootstrap import (
    build_runtime,
    check_runtime_start,
    dispose_runtime,
    log_tensorrt_models,
)
from app.config.settings import settings
from app.repositories.qdrant.memory_repository import QdrantStack
from app.services.memory_service import MemoryService
from app.services.re_embedding_service import ReEmbeddingService


@pytest.fixture
def bootstrap_lines(monkeypatch, caplog):
    """What ``app.bootstrap`` says at INFO (logging set up by a server test disables the loggers that exist)."""
    monkeypatch.setattr(bootstrap.logger, "disabled", False)
    caplog.set_level(logging.INFO, logger=bootstrap.logger.name)
    return lambda: [record.getMessage() for record in caplog.records if record.name == bootstrap.logger.name]


@pytest.fixture
async def built_runtime(runtime_settings):
    runtime = await build_runtime()
    yield runtime
    await dispose_runtime(runtime)


async def test_the_runtime_is_wired_to_one_repository_on_its_stack(built_runtime):
    repository = built_runtime.services.memory.memory_repo

    assert type(built_runtime.stack) is QdrantStack
    assert type(built_runtime.services.memory) is MemoryService
    assert type(built_runtime.services.re_embedding) is ReEmbeddingService
    assert repository.store is built_runtime.stack.store
    assert repository.schema is built_runtime.stack.schema


async def test_the_runtime_hands_the_same_repository_and_the_models_to_its_services(
    built_runtime, tensorrt_embedder, tensorrt_reranker,
):
    repository = built_runtime.services.memory.memory_repo

    assert repository.embedding_adapter is tensorrt_embedder
    assert repository.rerank_adapter is tensorrt_reranker
    assert built_runtime.services.re_embedding.memory_repository is repository
    assert built_runtime.services.re_embedding.embedding_adapter is tensorrt_embedder
    assert built_runtime.adapters == (tensorrt_embedder, tensorrt_reranker)


async def test_a_runtime_without_reranking_has_no_reranker_anywhere(runtime_settings, monkeypatch, tensorrt_embedder):
    monkeypatch.setattr(settings, "RERANKING_ENABLED", False)

    runtime = await build_runtime()
    try:
        assert runtime.services.memory.memory_repo.rerank_adapter is None
        assert runtime.adapters == (tensorrt_embedder, None)
    finally:
        await dispose_runtime(runtime)


@pytest.mark.parametrize(
    ("ceiling", "permits"),
    [
        ("*", lambda tool: True),
        ("read", lambda tool: not tool.mutates),
        ("write", lambda tool: tool.mutates),
    ],
)
async def test_the_runtime_permits_the_tools_that_the_scope_ceiling_allows(runtime_settings, monkeypatch, ceiling, permits):
    monkeypatch.setattr(settings, "AXMEM_SCOPES", ceiling)

    runtime = await build_runtime()
    try:
        tools = runtime.registry.list_all_tools()
        assert runtime.instance_scopes == frozenset({ceiling})
        assert runtime.permitted_tools == {tool.name for tool in tools if permits(tool)}
        assert 0 < len(runtime.permitted_tools) <= len(tools)
        assert (len(runtime.permitted_tools) == len(tools)) is (ceiling == "*")
    finally:
        await dispose_runtime(runtime)


async def test_the_runtime_says_each_step_of_its_start_in_order(
    runtime_settings, tensorrt_embedder, tensorrt_reranker, bootstrap_lines,
):
    runtime = await build_runtime()
    try:
        categories = runtime.registry.list_categories()
        lines = bootstrap_lines()

        assert lines[0] == "Embedding adapters initialized"
        assert lines[1:3] == [tensorrt_embedder.startup.summary(), tensorrt_reranker.startup.summary()]
        assert lines[3].startswith("GPU memory with the embedder and reranker loaded: ")
        assert lines[4:] == [
            "Qdrant collections ready",
            f"Tool registration complete: {len(runtime.registry.list_all_tools())} tools across {len(categories)} categories",
            f"Categories: {categories}",
            f"Scoped permissions: scopes=['*'], permitted_tools={len(runtime.permitted_tools)}",
        ]
    finally:
        await dispose_runtime(runtime)


async def test_a_start_check_on_a_ready_node_passes_and_leaves_the_collections_in_place(qdrant_env, monkeypatch):
    monkeypatch.setattr(settings, "AXMEM_SCOPES", "*")

    assert await check_runtime_start() is None
    assert await check_runtime_start() is None


# ============================================
# What the models say at start-up
# ============================================


def test_both_models_are_logged_with_the_memory_and_the_context_they_share(tensorrt_embedder, tensorrt_reranker, bootstrap_lines):
    log_tensorrt_models(tensorrt_embedder, tensorrt_reranker, None)

    lines = bootstrap_lines()
    assert lines[:2] == [tensorrt_embedder.startup.summary(), tensorrt_reranker.startup.summary()]
    sharing = f"one CUDA context ({tensorrt_embedder.startup.cuda_context:#x}) shared by the embedder and reranker"
    assert len(lines) == 3
    assert lines[2].startswith("GPU memory with the embedder and reranker loaded: this process holds ")
    assert lines[2].endswith(f"; {sharing}")


def test_one_model_is_logged_with_its_own_role(tensorrt_embedder, bootstrap_lines):
    log_tensorrt_models(tensorrt_embedder, None)

    lines = bootstrap_lines()
    assert lines[0] == tensorrt_embedder.startup.summary()
    assert len(lines) == 2
    assert lines[1].startswith("GPU memory with the embedder loaded: ")
    assert lines[1].endswith(f"; one CUDA context ({tensorrt_embedder.startup.cuda_context:#x}) shared by the embedder")


def test_models_on_different_cuda_contexts_are_reported_as_a_warning_and_in_the_memory_line(
    tensorrt_embedder, tensorrt_reranker, bootstrap_lines, caplog,
):
    other = SimpleNamespace(startup=replace(tensorrt_reranker.startup, cuda_context=1))

    log_tensorrt_models(tensorrt_embedder, other)

    warning = "2 different CUDA contexts for the embedder and reranker, which should not happen"
    assert [record.getMessage() for record in caplog.records if record.levelno == logging.WARNING] == [warning]
    lines = bootstrap_lines()
    assert len(lines) == 4
    assert lines[3].startswith("GPU memory with the embedder and reranker loaded: ")
    assert lines[3].endswith(f"; {warning}")

