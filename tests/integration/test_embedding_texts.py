# SPDX-FileCopyrightText: 2026 Giovanni Manzoni
# SPDX-License-Identifier: AGPL-3.0-only

"""The cosine values that the tests rely on, under the production embedder.

``tests/embedding_texts.py`` holds texts that are clearly similar or clearly unrelated; the auto-link
cutoff, the obsolete warning and the ordering of query results are tested with them. A new model or a
change of numerics moves the values, and this module fails first and says which pair moved.
"""
import pytest

from app.config.settings import settings
from app.models.memory_models import MemoryCreate
from tests.embedding_doubles import similarity
from tests.embedding_texts import (
    BAKING_CONTENT,
    BAKING_CONTEXT,
    BAKING_TITLE,
    FEEDING_CONTENT,
    FEEDING_CONTEXT,
    FEEDING_QUERY,
    FEEDING_TITLE,
    POOL_CONTENT,
    POOL_CONTEXT,
    POOL_TITLE,
)

TOLERANCE = 0.03


def memory(title: str, content: str, context: str) -> MemoryCreate:
    return MemoryCreate(title=title, content=content, context=context, keywords=[], tags=[], importance=7)


FEEDING_ZERO = memory(f"{FEEDING_TITLE} 0", FEEDING_CONTENT, FEEDING_CONTEXT)
FEEDING_THREE = memory(f"{FEEDING_TITLE} 3", FEEDING_CONTENT, FEEDING_CONTEXT)
BAKING = memory(f"{BAKING_TITLE} 0", BAKING_CONTENT, BAKING_CONTEXT)
POOL = memory(POOL_TITLE, POOL_CONTENT, POOL_CONTEXT)


@pytest.mark.parametrize(
    ("left", "right", "expected"),
    [
        pytest.param(FEEDING_ZERO, FEEDING_THREE, 0.99, id="feeding-to-feeding"),
        pytest.param(FEEDING_ZERO, BAKING, 0.83, id="feeding-to-baking"),
        pytest.param(FEEDING_ZERO, POOL, 0.46, id="feeding-to-pool"),
        pytest.param(BAKING, POOL, 0.37, id="baking-to-pool"),
        pytest.param(FEEDING_QUERY, FEEDING_ZERO, 0.88, id="query-to-feeding"),
        pytest.param(FEEDING_QUERY, BAKING, 0.78, id="query-to-baking"),
    ],
)
async def test_the_pairs_keep_the_similarity_the_tests_rely_on(tensorrt_embedder, left, right, expected):
    assert await similarity(tensorrt_embedder, left, right) == pytest.approx(expected, abs=TOLERANCE)


async def test_the_pairs_stay_on_the_right_side_of_the_thresholds(tensorrt_embedder):
    related = await similarity(tensorrt_embedder, FEEDING_ZERO, BAKING)
    unrelated = await similarity(tensorrt_embedder, FEEDING_ZERO, POOL)
    duplicate = await similarity(tensorrt_embedder, FEEDING_ZERO, FEEDING_THREE)

    assert settings.MEMORY_SIMILARITY_THRESHOLD < related < settings.OBSOLETE_WARNING_THRESHOLD
    assert unrelated < settings.MEMORY_SIMILARITY_THRESHOLD
    assert duplicate > settings.OBSOLETE_WARNING_THRESHOLD
