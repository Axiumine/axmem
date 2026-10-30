# SPDX-FileCopyrightText: 2026 Giovanni Manzoni
# SPDX-License-Identifier: AGPL-3.0-only

"""Memory creation, updates and obsolescence for the Qdrant memory repository.

Edits and obsolete toggles are compare-and-set writes. The version a writer read is part of the write
itself, so a writer that lost a race changes nothing and Qdrant does not say so: it reads the point back and
looks for the claim token of its write among the tokens the point keeps (later writes add theirs and do not
erase it). When the token is not there it starts again from the new state.
"""
import logging
import uuid
from datetime import UTC, datetime
from typing import Any, cast
from uuid import UUID

from app.exceptions import (
    AssociationNotFoundError,
    ConflictError,
    InvalidStateTransitionError,
    NotFoundError,
)
from app.models.memory_models import Memory, MemoryCreate, MemoryUpdate
from app.repositories.qdrant.memory_base import MAX_CAS_ATTEMPTS
from app.repositories.qdrant.memory_reads import QdrantMemoryReadMixin
from app.repositories.qdrant.payloads import (
    edited_payload,
    memory_from_point,
    memory_payload,
    new_token,
    stamp,
)
from app.repositories.qdrant.schema import Written
from app.repositories.qdrant.store import (
    VERSION_KEY,
    NewPoint,
    Point,
    Scope,
    Where,
    claim_fields,
    equals,
)
from app.utils.embedding_helpers import build_embedding_text

logger = logging.getLogger(__name__)


def _conflict(memory_id: UUID) -> ConflictError:
    return ConflictError(
        f"Memory {memory_id} kept changing while this write was applied; nothing was changed, try again",
    )


class QdrantMemoryWriteMixin(QdrantMemoryReadMixin):
    """Memory writes: create, update, obsolete and restore"""

    async def create_memory(self, user_id: UUID, memory: MemoryCreate) -> Memory:
        """Create a new memory

        The id is a time-ordered UUID generated here. The point is created with ``insert_only``, so a
        repeated request (after a timeout) cannot overwrite what the first one stored. The point goes into the live
        generation by name, after the check that it holds vectors of this server's embedder; when the alias moves
        during the write, the point is written into the new live generation too.

        Raises:
            EmbedderChangedError: If ``axmem re-embed`` switched the memories to another embedder while the server ran.
                Raised before the write, nothing was stored; raised after it (the switch came during the write), the
                memory is in the previous generation and may or may not have been carried over, which the text says
        """
        scope = Scope.of(user_id)
        vector = await self._generate_embeddings(build_embedding_text(memory_data=memory))
        point = NewPoint(
            id=uuid.uuid7(),
            vector=vector,
            payload=memory_payload(user_id, memory, token=new_token(), now=datetime.now(UTC)),
        )
        generation = await self.schema.writable_generation()
        while True:
            await self.store.insert(scope, generation, [point])
            live = await self.schema.writable_generation(written=Written.MEMORY)
            if live == generation:
                return memory_from_point(Point(point.id, point.payload))
            # The alias moved while the point was written, so it went to a generation that is no longer live (and a copy
            # of the memories may have passed it by). Write it again into the live one; insert-only leaves a copy as it is
            generation = live

    async def update_memory(self, user_id: UUID, memory_id: UUID, updated_memory: MemoryUpdate) -> Memory:
        """Apply the changes to a memory as one conditional write of the whole point.

        The embedding is generated before the write, with no lock held. A write that loses to another
        writer is repeated from the new state, and a text that was embedded already is not embedded twice.
        After MAX_CAS_ATTEMPTS lost races the update fails with ConflictError and changes nothing.

        Raises:
            NotFoundError: If the memory does not exist for this tenant
            ConflictError: If concurrent changes kept the write from being applied
            OutcomeUnknownError: If so many writes landed after this one that it cannot be told whether it applied
            EmbedderChangedError: If ``axmem re-embed`` switched the memories to another embedder while the server ran
                (an edit that had landed in the previous generation says that it may or may not have been carried over)
        """
        scope = Scope.of(user_id)
        changes = updated_memory.model_dump(exclude_unset=True)
        embedded: dict[str, list[float]] = {}
        for attempt in range(1, MAX_CAS_ATTEMPTS + 1):
            memory = await self._try_update(scope, memory_id, changes, embedded)
            if memory is not None:
                return memory
            logger.info("memory changed while it was being updated, trying again", extra={
                "memory_id": str(memory_id),
                "user_id": str(user_id),
                "attempt": attempt,
            })
        raise _conflict(memory_id)

    async def _try_update(
        self, scope: Scope, memory_id: UUID, changes: dict[str, Any], embedded: dict[str, list[float]],
    ) -> Memory | None:
        """One attempt: read the memory, write the change conditional on its version, confirm that it applied.

        An attempt works on one generation of the memory collection from start to end, because it writes back the
        vector it read unless the text changed, and a vector read from one generation must not be written into
        another (a re-embedding moves the live alias to a generation with vectors of a different model). When the
        alias moved during the attempt, the write went to a generation that is no longer live and is not confirmed:
        the next attempt makes the change again on the live one, unless that one holds the vectors of another embedder,
        which ends the edit with an ``EmbedderChangedError`` that says the outcome is open. None means the attempt did not apply.
        """
        generation = await self.schema.writable_generation()
        current = await self._current(scope, memory_id, generation=generation, with_vectors=True)
        version = int(current.payload[VERSION_KEY])
        token = new_token()
        payload = edited_payload(current.payload, changes, token=token, now=datetime.now(UTC))
        before = build_embedding_text(memory_data=memory_from_point(current))
        after = build_embedding_text(memory_data=memory_from_point(Point(memory_id, payload)))
        vector = cast(list[float], current.vector)  # read with_vectors=True
        if after != before:
            if after not in embedded:
                embedded[after] = await self._generate_embeddings(after)
            vector = embedded[after]

        await self.store.replace(scope, generation, NewPoint(memory_id, vector, payload), expected_version=version)
        if not await self.store.landed(scope, generation, memory_id, from_version=version, token=token):
            return None
        if await self.schema.writable_generation(written=Written.MEMORY) != generation:
            return None
        return (await self._memories(scope, [Point(memory_id, payload)]))[0]

    async def mark_obsolete(
        self,
        user_id: UUID,
        memory_id: UUID,
        reason: str,
        superseded_by: UUID | None = None,
    ) -> bool:
        """Mark a memory as obsolete (soft delete)

        Raises:
            NotFoundError: If memory not found or doesn't belong to the tenant
            AssociationNotFoundError: If superseded_by memory not found or doesn't belong to the tenant
            InvalidStateTransitionError: If the memory is already obsolete
            ConflictError: If concurrent changes kept the write from being applied
        """
        scope = Scope.of(user_id)
        if superseded_by is not None and not await self.store.get_agreed(
            scope, self.memories, [superseded_by], with_payload=False,
        ):
            raise AssociationNotFoundError(f"Superseding memory {superseded_by} not found")

        def obsolete(now: datetime) -> dict[str, Any]:
            return {
                "is_obsolete": True,
                "obsolete_reason": reason,
                "superseded_by": str(superseded_by) if superseded_by else None,
                "obsoleted_at": stamp(now),
            }

        return await self._toggle(scope, memory_id, was_obsolete=False, changes=obsolete)

    async def restore_obsolete(self, user_id: UUID, memory_id: UUID) -> bool:
        """Clear obsolescence and return the memory to active search.

        Returns True only when the memory was obsolete and now is not; a missing or active memory is False.
        """
        def restore(_now: datetime) -> dict[str, Any]:
            return {"is_obsolete": False, "obsolete_reason": None, "superseded_by": None, "obsoleted_at": None}

        try:
            return await self._toggle(Scope.of(user_id), memory_id, was_obsolete=True, changes=restore)
        except (NotFoundError, InvalidStateTransitionError):
            return False

    async def _toggle(self, scope: Scope, memory_id: UUID, was_obsolete: bool, changes: Any) -> bool:
        """Flip the obsolete state with a write that applies only while the version and the state are the ones read."""
        for _ in range(MAX_CAS_ATTEMPTS):
            current = await self._current(scope, memory_id)
            if bool(current.payload["is_obsolete"]) != was_obsolete:
                state = "already obsolete" if not was_obsolete else "not obsolete"
                raise InvalidStateTransitionError(f"Memory {memory_id} is {state}")
            now = datetime.now(UTC)
            version = int(current.payload[VERSION_KEY])
            token = new_token()
            await self.store.patch(
                scope,
                self.memories,
                memory_id,
                {**changes(now), "updated_at": stamp(now), VERSION_KEY: version + 1, **claim_fields(current.payload, token)},
                expected=Where().also(equals(VERSION_KEY, version), equals("is_obsolete", was_obsolete)),
            )
            if await self.store.landed(scope, self.memories, memory_id, from_version=version, token=token):
                return True
        raise _conflict(memory_id)

    async def _current(
        self, scope: Scope, memory_id: UUID, *, generation: str | None = None, with_vectors: bool = False,
    ) -> Point:
        """The memory as a majority of the replicas hold it (a write is decided on this), in ``generation`` or the live one."""
        points = await self.store.get_agreed(scope, generation or self.memories, [memory_id], with_vectors=with_vectors)
        if not points:
            raise NotFoundError(f"Memory {memory_id} not found")
        return points[0]
