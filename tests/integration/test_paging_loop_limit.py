"""The limit on page reads per test (``paging_loop_ends_the_test`` in tests/integration/conftest.py): a loop that never ends fails."""
import pytest

from app.repositories.qdrant.store import Scope
from tests.qdrant_store_support import TENANT, open_stack
from tests.qdrant_support import SCROLLS_PER_TEST


@pytest.fixture
async def stack(isolated_qdrant):
    async with open_stack() as opened:
        yield opened


async def test_the_page_after_the_limit_fails_the_test_and_the_pages_up_to_it_do_not(stack):
    scope = Scope.of(TENANT)
    collection = stack.schema.names.live

    for _ in range(SCROLLS_PER_TEST):
        await stack.store.scroll(scope, collection, limit=1)

    with pytest.raises(pytest.fail.Exception, match=f"more than {SCROLLS_PER_TEST} pages were read in one test"):
        await stack.store.scroll(scope, collection, limit=1)
