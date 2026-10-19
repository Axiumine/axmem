"""PostgreSQL activity log: querying events by the actor that caused them."""
import pytest
import pytest_asyncio

from app.models.activity_models import ActionType, ActivityEvent, ActorType, EntityType
from app.repositories.postgres.activity_repository import PostgresActivityRepository
from tests.e2e.pg_memory_repository_helpers import create_user

pytestmark = [pytest.mark.e2e, pytest.mark.asyncio(loop_scope="session")]


@pytest_asyncio.fixture(loop_scope="session")
async def owner(db_adapter):
    return await create_user(db_adapter, "activity-actor-e2e")


def _event(entity_id: int, actor: ActorType) -> ActivityEvent:
    return ActivityEvent(
        entity_type=EntityType.MEMORY,
        entity_id=entity_id,
        action=ActionType.CREATED,
        snapshot={"title": f"memory {entity_id}"},
        actor=actor,
    )


async def test_query_by_actor_returns_only_that_actors_events(db_adapter, owner):
    repository = PostgresActivityRepository(db_adapter)
    await repository.save_event(owner, _event(1, ActorType.USER))
    await repository.save_event(owner, _event(2, ActorType.SYSTEM))
    await repository.save_event(owner, _event(3, ActorType.SYSTEM))

    events, total = await repository.query_events(owner, actor=ActorType.SYSTEM)

    assert total == 2
    assert sorted(e.entity_id for e in events) == [2, 3]
    assert {e.actor for e in events} == {ActorType.SYSTEM}
