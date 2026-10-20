"""The migration that dropped the activity log and the memory access counters, on PostgreSQL."""

import pytest

from tests.activity_removal_cases import TestActivityRemoval as TestActivityRemoval

pytestmark = [pytest.mark.e2e, pytest.mark.asyncio(loop_scope="session")]


@pytest.fixture
def activity_db(db_adapter):
    return db_adapter
