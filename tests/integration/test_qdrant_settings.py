# SPDX-FileCopyrightText: 2026 Giovanni Manzoni
# SPDX-License-Identifier: AGPL-3.0-only

"""Validation of the Qdrant settings: a wrong value stops start-up with a message that names the setting.

Settings are built from environment variables with Settings(_env_file=None), as startup does.
"""
import pytest
from pydantic import ValidationError

from app.config.settings import Settings


def build(monkeypatch, **environment: str) -> Settings:
    for name, value in environment.items():
        monkeypatch.setenv(name, value)
    return Settings(_env_file=None)


def test_a_single_node_is_the_default(monkeypatch):
    monkeypatch.delenv("QDRANT_URL", raising=False)

    assert build(monkeypatch).qdrant_urls == ["http://127.0.0.1:6333"]


def test_several_nodes_are_trimmed_deduplicated_and_kept_in_order(monkeypatch):
    configured = build(monkeypatch, QDRANT_URL=" http://q1:6333/ ,https://q2:6333,, http://q1:6333 ")

    assert configured.qdrant_urls == ["http://q1:6333", "https://q2:6333"]


@pytest.mark.parametrize(
    ("url", "message"),
    [
        ("", "must name at least one Qdrant node"),
        (" , ,", "must name at least one Qdrant node"),
        ("q1:6333", "must start with http:// or https://"),
        ("http://q1:6333,ftp://q2", "must start with http:// or https://"),
    ],
)
def test_a_node_address_that_is_not_http_stops_start_up(monkeypatch, url, message):
    with pytest.raises(ValidationError, match=message):
        build(monkeypatch, QDRANT_URL=url)


@pytest.mark.parametrize("prefix", ["axmem", "Axmem-2_test", "9lives", "a" * 64])
def test_a_collection_prefix_of_letters_digits_underscores_and_hyphens_is_accepted(monkeypatch, prefix):
    assert prefix == build(monkeypatch, QDRANT_COLLECTION_PREFIX=prefix).QDRANT_COLLECTION_PREFIX


@pytest.mark.parametrize("prefix", ["", "_leading", "-leading", "has space", "dot.ted", "a" * 65, "slash/ed"])
def test_a_collection_prefix_that_could_not_be_a_collection_name_stops_start_up(monkeypatch, prefix):
    with pytest.raises(ValidationError, match="QDRANT_COLLECTION_PREFIX must be 1 to 64"):
        build(monkeypatch, QDRANT_COLLECTION_PREFIX=prefix)


def test_the_write_consistency_factor_cannot_exceed_the_replication_factor(monkeypatch):
    with pytest.raises(ValidationError, match="cannot exceed QDRANT_REPLICATION_FACTOR"):
        build(monkeypatch, QDRANT_REPLICATION_FACTOR="2", QDRANT_WRITE_CONSISTENCY_FACTOR="3")

    configured = build(monkeypatch, QDRANT_REPLICATION_FACTOR="3", QDRANT_WRITE_CONSISTENCY_FACTOR="2")

    assert (configured.QDRANT_REPLICATION_FACTOR, configured.QDRANT_WRITE_CONSISTENCY_FACTOR) == (3, 2)
