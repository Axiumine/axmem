"""The migration that dropped the skills tables, on PostgreSQL."""

import pytest

from tests.skills_removal_cases import (
    TestSkillsRemoval as TestSkillsRemoval,
)

pytestmark = [pytest.mark.e2e, pytest.mark.asyncio(loop_scope="session")]


@pytest.fixture
def skills_db(db_adapter):
    return db_adapter
