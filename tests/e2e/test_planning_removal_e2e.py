"""The migration that dropped the planning tables, on PostgreSQL."""

import pytest

from tests.planning_removal_cases import (
    TestPlanningRemoval as TestPlanningRemoval,
)

pytestmark = [pytest.mark.e2e, pytest.mark.asyncio(loop_scope="session")]


@pytest.fixture
def planning_db(db_adapter):
    return db_adapter
