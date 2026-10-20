"""Re-embedding for the Qdrant memory repository: new generations of the memory collection and targeted rebuilds.

Embedding every memory with another model means vectors of another width, which a collection cannot hold
next to the old ones. The memories are copied, with new vectors, into a new generation while the old one
keeps serving, and the live alias is then moved over. The copy is written so that it can never overwrite
a newer edit: a memory the new generation lacks is created with ``insert_only``, and one it holds at an
older version is replaced by a write that applies only while the stored version is still older.
"""
import logging
from uuid import UUID

from app.config.settings import settings
from app.protocols.memory_protocol import (
    EmbeddingWrites,
    StoredMemory,
    ValidationResult,
)
from app.repositories.qdrant.memory_base import QdrantMemoryBase
from app.repositories.qdrant.payloads import (
    new_token,
    payload_of,
    refreshed_payload,
    stored_from_point,
)
from app.repositories.qdrant.store import (
    SYSTEM,
    TOKEN_KEY,
    VERSION_KEY,
    NewPoint,
    Point,
    Scope,
    Where,
    equals,
    has_ids,
)

logger = logging.getLogger(__name__)


def _targeted_filter(memory_ids: list[UUID] | None, project: str | None) -> Where:
    """Active memories, optionally limited to explicit ids and to one project scope (besides the tenant)."""
    where = Where().also(equals("is_obsolete", False))
    if memory_ids:
        where = where.also(has_ids(memory_ids))
    if project is not None:
        where = where.also(equals("project", project))
    return where


class QdrantMemoryReembeddingMixin(QdrantMemoryBase):
    """Re-embedding every memory into a new generation, rebuilding a tenant's embeddings and validating them"""

    # ============ New generation of the memory collection (operator, every tenant) ============

    async def count_all_memories(self) -> int:
        """Count all memories, including obsolete ones, across all tenants."""
        return await self.store.count(SYSTEM, self.memories)

    async def live_generation(self) -> str:
        return await self.schema.live_generation()

    async def create_generation(self) -> str:
        return await self.schema.create_generation()

    async def activate_generation(self, generation: str) -> None:
        await self.schema.activate(generation)

    async def discard_generation(self, generation: str) -> None:
        await self.schema.discard(generation)

    async def outdated_in(
        self,
        source: str,
        target: str,
        limit: int,
        start: UUID | None,
    ) -> tuple[list[StoredMemory], UUID | None]:
        """Scan up to ``limit`` memories of ``source`` from the id ``start``; return those ``target`` lacks or holds an older version of.

        The second value is the id to continue from, or None when the scan reached the end of ``source``.
        """
        scanned = await self.store.scroll(SYSTEM, source, limit=limit + 1, start_from=start)
        batch, following = scanned[:limit], scanned[limit:]
        present = await self.store.get(SYSTEM, target, [point.id for point in batch], with_payload=[VERSION_KEY])
        held = {point.id: int(point.payload[VERSION_KEY]) for point in present}
        outdated = [
            stored_from_point(point) for point in batch
            if held.get(point.id, 0) < int(point.payload[VERSION_KEY])
        ]
        return outdated, following[0].id if following else None

    async def copy_into(self, target: str, items: list[tuple[StoredMemory, list[float]]]) -> int:
        """Write the memories with their new embeddings into ``target``; return how many were written.

        Versions start at 1, so "held at version 0" means "absent". A memory that is absent is created; one
        held at an older version is replaced by a write guarded on that; one held at the same or a newer
        version is left alone.
        """
        present = await self.store.get(
            SYSTEM, target, [stored.memory.id for stored, _ in items], with_payload=[VERSION_KEY],
        )
        held = {point.id: int(point.payload[VERSION_KEY]) for point in present}
        token = new_token()
        created = await self._create_absent(target, items, held, token)
        return created + await self._replace_older(target, items, held, token)

    async def _create_absent(
        self, target: str, items: list[tuple[StoredMemory, list[float]]], held: dict[UUID, int], token: str,
    ) -> int:
        points = [
            NewPoint(stored.memory.id, vector, payload_of(stored, token=token))
            for stored, vector in items if stored.memory.id not in held
        ]
        await self.store.insert(SYSTEM, target, points)
        return len(points)

    async def _replace_older(
        self, target: str, items: list[tuple[StoredMemory, list[float]]], held: dict[UUID, int], token: str,
    ) -> int:
        replaced = 0
        for stored, vector in items:
            if held.get(stored.memory.id, stored.version) >= stored.version:
                continue
            point = NewPoint(stored.memory.id, vector, payload_of(stored, token=token))
            await self.store.replace(SYSTEM, target, point, older_than=stored.version)
            replaced += 1
        if not replaced:
            return 0
        # The response does not say whether a guarded replace applied: count the ones carrying our token
        after = await self.store.get(
            SYSTEM, target, [stored.memory.id for stored, _ in items if stored.memory.id in held],
            with_payload=[TOKEN_KEY], quorum=True,
        )
        return sum(1 for point in after if point.payload.get(TOKEN_KEY) == token)

    async def validate_generation(self, generation: str) -> ValidationResult:
        """Check the memory count, the vector width and a similarity search of a generation (against the live one)."""
        live = await self.live_generation()
        count_ok = (
            await self.store.count(SYSTEM, generation) == await self.store.count(SYSTEM, live)
            if generation != live
            else True
        )
        sample = await self.store.scroll(SYSTEM, generation, limit=1, with_payload=False, with_vectors=True)
        width_ok = await self.schema.vector_width(generation) == settings.EMBEDDING_DIMENSIONS and all(
            point.vector is not None and len(point.vector) == settings.EMBEDDING_DIMENSIONS for point in sample
        )
        search_ok = True
        if sample and sample[0].vector is not None:
            hits = await self.store.search(SYSTEM, generation, query=sample[0].vector, limit=1)
            search_ok = bool(hits)
        return ValidationResult(count_ok=count_ok, dimensions_ok=width_ok, search_ok=search_ok)

    # ============ Targeted (tenant-scoped) rebuild ============

    async def count_memories_for_targeted_rebuild(
        self,
        user_id: UUID,
        memory_ids: list[UUID] | None = None,
        project: str | None = None,
    ) -> int:
        return await self.store.count(Scope.of(user_id), self.memories, _targeted_filter(memory_ids, project))

    async def project_exists_for_user(self, user_id: UUID, project: str) -> bool:
        return await self.store.count(Scope.of(user_id), self.memories, Where().also(equals("project", project))) > 0

    async def get_memories_for_targeted_rebuild(
        self,
        user_id: UUID,
        limit: int,
        after_id: UUID | None = None,
        memory_ids: list[UUID] | None = None,
        project: str | None = None,
    ) -> list[StoredMemory]:
        points = await self.store.scroll(
            Scope.of(user_id),
            self.memories,
            _targeted_filter(memory_ids, project),
            limit=limit + 1,
            start_from=after_id,
        )
        # The scan starts at after_id itself, which the caller has seen
        fresh = [point for point in points if point.id != after_id][:limit]
        return [stored_from_point(point) for point in fresh]

    async def upsert_targeted_embeddings(
        self,
        user_id: UUID,
        updates: list[tuple[UUID, list[float]]],
        expected_versions: dict[UUID, int],
    ) -> EmbeddingWrites:
        """Write new embeddings for active memories of the tenant, each only while the memory is at the version read.

        The write replaces the whole point, vector and payload, and raises the version by one, so an edit
        that read the old vector before this write cannot store it again over the new one.
        """
        scope = Scope.of(user_id)
        current = {
            point.id: point for point in await self.store.get(
                scope, self.memories, [memory_id for memory_id, _ in updates],
            )
        }
        result = EmbeddingWrites(written=[], failed=[])
        for memory_id, vector in updates:
            point = current.get(memory_id)
            version = expected_versions.get(memory_id)
            if point is None or point.payload["is_obsolete"] or point.payload[VERSION_KEY] != version:
                continue
            try:
                if await self._refresh(scope, point, vector):
                    result.written.append(memory_id)
            except Exception:  # noqa: BLE001 - one memory that cannot be written must not stop the others
                logger.exception("embedding write failed", extra={"memory_id": str(memory_id), "user_id": str(user_id)})
                result.failed.append(memory_id)
        return result

    async def _refresh(self, scope: Scope, point: Point, vector: list[float]) -> bool:
        """Replace one memory's vector with a write guarded on the version read; True when that write is the stored one."""
        token = new_token()
        payload = refreshed_payload(point.payload, token=token)
        await self.store.replace(
            scope, self.memories, NewPoint(point.id, vector, payload), expected_version=int(point.payload[VERSION_KEY]),
        )
        return (await self.store.tokens(scope, self.memories, [point.id])).get(point.id) == token
