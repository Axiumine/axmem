"""EventBus SSE stream edge cases: events without an owner, totals, and teardown after clear().

Complements ``test_event_bus.py`` (delivery, filtering, back-pressure) with the paths around the
subscriber registry: nothing is streamed for an ownerless event, the global subscriber count adds
up every user, and a stream torn down after ``clear()`` leaves the registry consistent.
"""

import asyncio
from uuid import UUID

from app.events import EventBus
from app.models.activity_models import ActionType, ActivityEvent, EntityType

USER_A = UUID("12345678-1234-5678-1234-567812345678")
USER_B = UUID("87654321-4321-8765-4321-876543218765")


def _event(user_id: str | None, entity_id: int = 1) -> ActivityEvent:
    return ActivityEvent(
        entity_type=EntityType.MEMORY,
        entity_id=entity_id,
        action=ActionType.CREATED,
        snapshot={"id": entity_id},
        user_id=user_id,
    )


async def _until(condition, attempts: int = 200) -> None:
    """Yield to the loop until ``condition()`` holds (no wall-clock sleeping)."""
    for _ in range(attempts):
        if condition():
            return
        await asyncio.sleep(0)
    raise AssertionError("condition never became true")


async def _subscribe(bus: EventBus, user_id: UUID, received: list) -> asyncio.Task:
    async def consume() -> None:
        async for item in bus.subscribe_stream(user_id):
            received.append(item)

    task = asyncio.create_task(consume())
    await _until(lambda: bus.stream_subscriber_count(str(user_id)) == 1)
    return task


async def _stop(task: asyncio.Task) -> None:
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)


async def test_event_without_owner_is_not_streamed_and_consumes_no_sequence_number():
    bus = EventBus()
    received: list = []
    task = await _subscribe(bus, USER_A, received)
    try:
        await bus.emit(_event(user_id=None, entity_id=1))
        await bus.emit(_event(user_id=str(USER_A), entity_id=2))
        await _until(lambda: received)

        assert [(item["entity_id"], item["seq"]) for item in received] == [(2, 1)]
    finally:
        await _stop(task)


async def test_subscriber_count_without_user_totals_every_user():
    bus = EventBus()
    tasks = [
        await _subscribe(bus, USER_A, []),
        await _subscribe(bus, USER_B, []),
    ]
    try:
        assert bus.stream_subscriber_count() == 2
        assert bus.stream_subscriber_count(str(USER_A)) == 1
        assert bus.stream_subscriber_count("nobody") == 0
    finally:
        for task in tasks:
            await _stop(task)


async def test_stream_stopped_after_bus_clear_leaves_no_subscribers():
    bus = EventBus()
    task = await _subscribe(bus, USER_A, [])

    bus.clear()
    await _stop(task)

    assert task.done()
    assert bus.stream_subscriber_count() == 0
    assert bus.stream_subscriber_count(str(USER_A)) == 0
