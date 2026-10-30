# SPDX-FileCopyrightText: 2026 Giovanni Manzoni
# SPDX-License-Identifier: AGPL-3.0-only

"""The names of the collections and the descriptions an operator reads: what a generation, an embedder and a layout say.

Nothing here asks the node: the names, the identity of the embedder, the differences of a layout and the types of the
indexes are worked out from the settings and from what the node would report.
"""
import re
import time
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from qdrant_client import models as qm

from app.config.settings import EMBEDDING_PROVIDERS, settings
from app.repositories.qdrant import schema as schema_module
from app.repositories.qdrant.schema import (
    TENANT_INDEX,
    CollectionNames,
    _describe_identity,
    _expected_index_type,
    _index_type,
    _layout_problems,
    embedder_identity,
)


def test_the_names_of_the_collections_derive_from_the_prefix():
    names = CollectionNames("deployment")

    assert names.prefix == "deployment"
    assert names.live == "deployment_memories"
    assert names.links == "deployment_links"
    assert names.links_staging == "deployment_links_relayout"


def test_a_generation_is_named_after_the_live_alias_the_moment_in_utc_and_four_random_characters(monkeypatch):
    class FrozenClock(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 3, 4, 5, 6, 7, tzinfo=UTC).astimezone(tz)

    monkeypatch.setenv("TZ", "Asia/Tokyo")
    time.tzset()
    monkeypatch.setattr(schema_module, "datetime", FrozenClock)

    try:
        names = [CollectionNames("deployment").new_generation() for _ in range(3)]
    finally:
        monkeypatch.undo()
        time.tzset()

    assert all(re.fullmatch(r"deployment_memories_20260304t050607[0-9a-f]{4}", name) for name in names)


def test_an_embedder_is_described_by_its_identity_or_as_unreadable():
    assert _describe_identity({"provider": "Ollama", "model": "m", "dimensions": 8}) == "provider Ollama, model m, dimensions 8"
    assert _describe_identity({}) == ""
    for unreadable in ("garbage", None, 42, ["provider", "Ollama"]):
        assert _describe_identity(unreadable) == "an unreadable record"


def test_the_identity_of_an_embedder_lists_only_what_its_provider_adds(monkeypatch):
    monkeypatch.setattr(settings, "EMBEDDING_MODEL", "some/model")
    monkeypatch.setattr(settings, "EMBEDDING_PROVIDER", " openai ")
    monkeypatch.setattr(settings, "EMBEDDING_TF32", True)
    monkeypatch.setattr(settings, "GATEWAY_BACKEND", "infinity")
    monkeypatch.setattr(settings, "GATEWAY_MODEL", "bge")

    assert embedder_identity(8) == {"provider": "OpenAI", "model": "some/model", "dimensions": 8}
    monkeypatch.setattr(settings, "EMBEDDING_PROVIDER", "tensorrt")
    assert embedder_identity(16) == {"provider": "TensorRT", "model": "some/model", "dimensions": 16, "precision": "tf32"}
    monkeypatch.setattr(settings, "EMBEDDING_TF32", False)
    assert embedder_identity(16)["precision"] == "fp32"
    monkeypatch.setattr(settings, "EMBEDDING_PROVIDER", "GATEWAY")
    assert embedder_identity(32) == {"provider": "gateway", "model": "some/model", "dimensions": 32, "gateway": "infinity/bge"}


def test_a_provider_that_is_not_one_of_the_providers_is_refused_by_the_name_of_the_setting(monkeypatch):
    monkeypatch.setattr(settings, "EMBEDDING_PROVIDER", "carrier-pigeon")

    with pytest.raises(ValueError) as refused:
        embedder_identity(8)

    assert str(refused.value) == f"Unsupported EMBEDDING_PROVIDER setting. Must be one of: {', '.join(EMBEDDING_PROVIDERS)}"


def test_each_difference_of_the_layout_is_named_with_the_setting_it_differs_from(monkeypatch):
    monkeypatch.setattr(settings, "QDRANT_SHARD_NUMBER", 1)
    monkeypatch.setattr(settings, "QDRANT_REPLICATION_FACTOR", 2)
    monkeypatch.setattr(settings, "QDRANT_WRITE_CONSISTENCY_FACTOR", 2)

    assert _layout_problems(SimpleNamespace(shard_number=3, replication_factor=4, write_consistency_factor=5)) == [
        "shard_number is 3 but QDRANT_SHARD_NUMBER is 1",
        "replication_factor is 4 but QDRANT_REPLICATION_FACTOR is 2",
        "write_consistency_factor is 5 but QDRANT_WRITE_CONSISTENCY_FACTOR is 2",
    ]
    assert _layout_problems(SimpleNamespace(shard_number=1, replication_factor=4, write_consistency_factor=2)) == [
        "replication_factor is 4 but QDRANT_REPLICATION_FACTOR is 2",
    ]
    assert _layout_problems(SimpleNamespace(shard_number=1, replication_factor=2, write_consistency_factor=2)) == []


def test_an_index_is_compared_by_the_name_of_its_type_and_a_tenant_index_is_a_keyword_index():
    assert _index_type(SimpleNamespace(data_type=qm.PayloadSchemaType.DATETIME)) == "datetime"
    assert _index_type(SimpleNamespace(data_type=qm.PayloadSchemaType.BOOL)) == "bool"
    assert _expected_index_type(TENANT_INDEX) == "keyword"
    assert _expected_index_type(qm.KeywordIndexParams(type=qm.KeywordIndexType.KEYWORD)) == "keyword"
    assert _expected_index_type(qm.PayloadSchemaType.INTEGER) == "integer"
    assert _expected_index_type(qm.PayloadSchemaType.DATETIME) == "datetime"
