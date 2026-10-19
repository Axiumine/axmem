"""The migration that dropped the entity tables, on PostgreSQL."""

import pytest

from tests.entities_removal_cases import TestEntitiesRemoval as TestEntitiesRemoval

pytestmark = [pytest.mark.e2e, pytest.mark.asyncio(loop_scope="session")]


@pytest.fixture
def entities_db(db_adapter):
    return db_adapter
