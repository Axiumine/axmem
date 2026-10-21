"""What the tool catalogue says a tool returns is what the adapter returns.

The catalogue once said that ``mark_memory_obsolete`` and ``unobsolete_memory`` return a Boolean and that
``link_memories`` returns a list of memory ids, while the adapters answer ``{"success": true}`` and
``{"linked_memory_ids": [...]}``. The tools run through the registry of a real runtime, and the text of the catalogue
is checked against the answers.
"""
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.bootstrap import build_runtime, dispose_runtime
from app.config.settings import settings
from app.exceptions import InvalidStateTransitionError, NotFoundError
from app.models.memory_models import MemoryCreate
from app.models.tenant_models import Tenant

CONTEXT = SimpleNamespace(fastmcp=SimpleNamespace(auth=None, trusted_local=True))


def _memory(title: str, content: str) -> MemoryCreate:
    return MemoryCreate(
        title=title,
        content=content,
        context="Checking the return values of the link and obsolete tools",
        keywords=["catalogue"],
        tags=["testing"],
        importance=5,
    )


@pytest.fixture
async def runtime(runtime_settings):
    built = await build_runtime()
    yield built
    await dispose_runtime(built)


@pytest.fixture
async def two_memories(runtime):
    """Two memories on unrelated subjects, so that nothing links them when they are created."""
    created = [
        await runtime.services.memory.create_memory(user_id=Tenant(settings.DEFAULT_TENANT).id, memory_data=_memory(title, content))
        for title, content in (
            ("Sourdough starter feeding", "Feed the sourdough starter with equal weights of flour and water every day."),
            ("Rust borrow checker", "A mutable borrow excludes every other borrow of the same value in Rust."),
        )
    ]
    return [memory for memory, _ in created]


def _returns(runtime, name: str) -> str:
    return next(tool.returns for tool in runtime.registry.list_all_tools() if tool.name == name)


async def _run(runtime, tool, **arguments):
    return await runtime.registry.execute(tool, arguments, ctx=CONTEXT)


async def test_link_memories_answers_with_the_targets_that_were_newly_linked(runtime, two_memories):
    first, second = two_memories

    linked = await _run(runtime, "link_memories", memory_id=first.id, related_ids=[second.id])
    again = await _run(runtime, "link_memories", memory_id=first.id, related_ids=[second.id, uuid4()])

    assert linked == {"linked_memory_ids": [second.id]}
    assert again == {"linked_memory_ids": []}  # the link exists, the other memory does not: both skipped
    assert "linked_memory_ids" in _returns(runtime, "link_memories")
    assert "newly linked" in _returns(runtime, "link_memories")
    assert "List of memory IDs" not in _returns(runtime, "link_memories")


async def test_the_obsolete_tools_answer_success_true_and_raise_on_every_failure(runtime, two_memories):
    memory = two_memories[0]

    obsolete = await _run(runtime, "mark_memory_obsolete", memory_id=memory.id, reason="Superseded in the test")
    with pytest.raises(InvalidStateTransitionError, match="already obsolete"):
        await _run(runtime, "mark_memory_obsolete", memory_id=memory.id, reason="Superseded again")
    restored = await _run(runtime, "unobsolete_memory", memory_id=memory.id)
    with pytest.raises(InvalidStateTransitionError, match="is not obsolete"):
        await _run(runtime, "unobsolete_memory", memory_id=memory.id)
    with pytest.raises(NotFoundError):
        await _run(runtime, "mark_memory_obsolete", memory_id=uuid4(), reason="No such memory")
    with pytest.raises(NotFoundError):
        await _run(runtime, "unobsolete_memory", memory_id=uuid4())

    assert obsolete == {"success": True}
    assert restored == {"success": True}


@pytest.mark.parametrize("tool", ["mark_memory_obsolete", "unobsolete_memory"])
async def test_the_catalogue_says_success_true_and_never_false_for_the_obsolete_tools(runtime, tool):
    returns = _returns(runtime, tool)

    assert '{"success": true}' in returns
    assert "never false" in returns
    assert "Boolean" not in returns
