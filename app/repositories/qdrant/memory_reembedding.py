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
from app.exceptions import OperatorCommandError
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
from app.repositories.qdrant.schema import LINK_VECTOR
from app.repositories.qdrant.store import (
    SYSTEM,
    VERSION_KEY,
    NewPoint,
    Point,
    Scope,
    Where,
    equals,
    has_ids,
)

logger = logging.getLogger(__name__)

# Links written to the new links collection in one request while it is rebuilt
LINK_COPY_BATCH = 256


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

        The second value is the id to continue from, or None when the scan reached the end of ``source``. The page is
        complete (``QdrantStore.page``): a memory that is being rewritten is awaited and never left out, and if the
        replicas will not agree about one the scan raises ``ConflictError``.
        """
        batch, following = await self.store.page(SYSTEM, source, limit=limit, start_from=start, quorum=True)
        present = await self.store.get_agreed(SYSTEM, target, [point.id for point in batch], with_payload=[VERSION_KEY])
        held = {point.id: int(point.payload[VERSION_KEY]) for point in present}
        outdated = [
            stored_from_point(point) for point in batch
            if held.get(point.id, 0) < int(point.payload[VERSION_KEY])
        ]
        return outdated, following

    async def copy_into(self, target: str, items: list[tuple[StoredMemory, list[float]]]) -> int:
        """Write the memories with their new embeddings into ``target``; return how many of them ``target`` holds afterwards.

        Versions start at 1, so "held at version 0" means "absent". A memory that is absent is created; one
        held at an older version is replaced by a write guarded on that; one held at the same or a newer
        version is left alone. The answer to a conditional write does not say whether it applied, so the memories
        written are read back and counted when ``target`` holds them at the version written or a newer one: a write
        that another writer overtook still counts, because the memory is at least as new as the copy, and one that
        did not take effect does not.
        """
        present = await self.store.get_agreed(
            SYSTEM, target, [stored.memory.id for stored, _ in items], with_payload=[VERSION_KEY],
        )
        held = {point.id: int(point.payload[VERSION_KEY]) for point in present}
        token = new_token()
        written = await self._create_absent(target, items, held, token) + await self._replace_older(target, items, held, token)
        if not written:
            return 0
        after = await self.store.get_agreed(
            SYSTEM, target, [stored.memory.id for stored in written], with_payload=[VERSION_KEY],
        )
        now = {point.id: int(point.payload[VERSION_KEY]) for point in after}
        return sum(1 for stored in written if now.get(stored.memory.id, 0) >= stored.version)

    async def _create_absent(
        self, target: str, items: list[tuple[StoredMemory, list[float]]], held: dict[UUID, int], token: str,
    ) -> list[StoredMemory]:
        """Create the memories ``target`` does not hold; return the ones a write was sent for."""
        absent = [(stored, vector) for stored, vector in items if stored.memory.id not in held]
        await self.store.insert(
            SYSTEM, target, [NewPoint(stored.memory.id, vector, payload_of(stored, token=token)) for stored, vector in absent],
        )
        return [stored for stored, _ in absent]

    async def _replace_older(
        self, target: str, items: list[tuple[StoredMemory, list[float]]], held: dict[UUID, int], token: str,
    ) -> list[StoredMemory]:
        """Replace the memories ``target`` holds at an older version; return the ones a write was sent for."""
        replaced: list[StoredMemory] = []
        for stored, vector in items:
            if held.get(stored.memory.id, stored.version) >= stored.version:
                continue
            point = NewPoint(stored.memory.id, vector, payload_of(stored, token=token))
            await self.store.replace(SYSTEM, target, point, older_than=stored.version)
            replaced.append(stored)
        return replaced

    async def rebuild_links(self, *, unfinished_only: bool = False) -> bool:
        """Give the links collection the layout the settings describe; return whether it had to be rebuilt.

        ``unfinished_only`` leaves a links collection that is whole alone and finishes only a rebuild that an earlier run
        left half way (its staging collection exists), which is what a run does before it embeds anything.

        A collection keeps the shard count, replication factor and write consistency factor it was created with, so
        the links follow a new layout the way the memories do, by moving into a collection made with it. A collection
        cannot be renamed, so the links pass through a staging collection: copied there (in the new layout), the
        old collection removed, a new one made in its place, and copied back. Each step checks what exists first and the
        copies leave existing points alone, so a run that stopped half way is finished by running this again. The old
        collection is removed only after the staged copy holds every link, and the staged copy only after the
        new collection does. Nothing else may write links meanwhile (the re-embedding runs with the server stopped).
        """
        links, staging = self.links, self.schema.names.links_staging
        staged = await self.schema.exists(staging)
        differs = await self.schema.links_layout_differs()
        if not staged and (unfinished_only or not differs):
            return False
        if differs:
            # The links collection is still whole: stage a fresh copy in the new layout, then remove the old collection
            if staged:
                await self.schema.discard(staging)
            await self.schema.create_links_collection(staging)
            await self._copy_links(links, staging)
            await self._require_links(staging, links)
            await self.schema.discard(links)
        # The staged copy holds every link: make the collection in the new layout and fill it
        await self.schema.create_links_collection(links)
        await self._copy_links(staging, links)
        await self._require_links(links, staging)
        await self.schema.discard(staging)
        logger.info("Rebuilt the links collection in the configured layout", extra={"collection": links})
        return True

    async def _copy_links(self, source: str, target: str) -> None:
        batch: list[NewPoint] = []
        async for point in self.store.iterate(SYSTEM, source, quorum=True):
            batch.append(NewPoint(point.id, LINK_VECTOR, point.payload))
            if len(batch) >= LINK_COPY_BATCH:
                await self.store.insert(SYSTEM, target, batch)
                batch = []
        await self.store.insert(SYSTEM, target, batch)

    async def _require_links(self, copy: str, original: str) -> None:
        """Refuse to go on, and so to remove ``original``, unless ``copy`` holds at least as many links."""
        held, expected = await self.store.count(SYSTEM, copy), await self.store.count(SYSTEM, original)
        if held < expected:
            raise OperatorCommandError(
                f"Rebuilding the links stopped: {copy} holds {held} links but {original} holds {expected}. "
                f"Nothing was removed; check Qdrant and run `axmem re-embed` again",
            )

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
        that read the old vector before this write cannot store it again over the new one. Nothing is written, and
        ``EmbedderChangedError`` is raised, when the live generation holds vectors of another embedder than this process's.
        """
        scope = Scope.of(user_id)
        generation = await self.schema.writable_generation()
        current = {
            point.id: point for point in await self.store.get(
                scope, generation, [memory_id for memory_id, _ in updates], quorum=True,
            )
        }
        result = EmbeddingWrites(written=[], failed=[])
        for memory_id, vector in updates:
            point = current.get(memory_id)
            version = expected_versions.get(memory_id)
            if point is None or point.payload["is_obsolete"] or point.payload[VERSION_KEY] != version:
                continue
            try:
                if await self._refresh(scope, generation, point, vector):
                    result.written.append(memory_id)
            except Exception:  # noqa: BLE001 - one memory that cannot be written must not stop the others
                logger.exception("embedding write failed", extra={"memory_id": str(memory_id), "user_id": str(user_id)})
                result.failed.append(memory_id)
        return result

    async def _refresh(self, scope: Scope, generation: str, point: Point, vector: list[float]) -> bool:
        """Replace one memory's vector with a write guarded on the version read; True when that write is the stored one."""
        token = new_token()
        version = int(point.payload[VERSION_KEY])
        payload = refreshed_payload(point.payload, token=token)
        await self.store.replace(scope, generation, NewPoint(point.id, vector, payload), expected_version=version)
        return await self.store.landed(scope, generation, point.id, from_version=version, token=token)
