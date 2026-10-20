"""Memory-to-memory links for the Qdrant memory repository.

A link is a point of its own in the links collection. Its id is derived from the tenant and the two memory
ids, so the same pair always names the same point, and it is created with ``insert_only``: creating a link
twice, from two requests at once, still stores one. Qdrant has no atomic append to a payload array, which
is why a link is not a list on the memory.
"""
import logging
from datetime import UTC, datetime
from uuid import UUID

from app.models.memory_models import Memory
from app.repositories.qdrant.memory_base import QdrantMemoryBase
from app.repositories.qdrant.payloads import link_id, link_payload, new_token
from app.repositories.qdrant.schema import LINK_VECTOR
from app.repositories.qdrant.store import (
    TOKEN_KEY,
    NewPoint,
    Scope,
    Where,
    equals,
    has_ids,
)

logger = logging.getLogger(__name__)

# Linked memories are read in one request; a memory with more links than this keeps its most important ones
MAX_LINK_CANDIDATES = 1000


class QdrantMemoryLinkMixin(QdrantMemoryBase):
    """Reading, creating and removing the bidirectional links between memories"""

    async def get_linked_memories(
        self,
        user_id: UUID,
        memory_id: UUID,
        project: str | None,
        max_links: int = 5,
        exclude_ids: list[UUID] | None = None,
    ) -> list[Memory]:
        """Get memories linked to a specific memory (1-hop neighbors), most important first

        Args:
            user_id: Tenant
            memory_id: Memory ID of the memory to retrieve linked memories for
            max_links: Maximum number of linked memories to return
            project: Optional project scope to keep linked memories to
            exclude_ids: Optional memory IDs to leave out, applied before max_links
        """
        scope = Scope.of(user_id)
        excluded = set(exclude_ids or ()) | {memory_id}
        neighbours = [
            other for other in (await self._linked_ids(scope, [memory_id]))[memory_id] if other not in excluded
        ][:MAX_LINK_CANDIDATES]
        if not neighbours:
            return []

        where = Where().also(equals("is_obsolete", False))
        if project:
            where = where.also(equals("project", project))
        points = await self.store.scroll(
            scope, self.memories, where.also(has_ids(neighbours)), limit=len(neighbours),
        )
        wanted = sorted(points, key=lambda point: (-int(point.payload["importance"]), str(point.id)))
        return await self._memories(scope, wanted[:max_links])

    async def create_links_batch(self, user_id: UUID, source_id: UUID, target_ids: list[UUID]) -> list[UUID]:
        """Create links from one memory to many others

        Targets that do not exist for the tenant, the source itself and links that exist already are
        skipped.

        Returns:
           The target ids that were newly linked, in the order given
        """
        scope = Scope.of(user_id)
        targets = await self._linkable_targets(scope, source_id, target_ids)
        if not targets:
            return []

        token = new_token()
        now = datetime.now(UTC)
        ids = {target: link_id(user_id, source_id, target) for target in targets}
        await self.store.insert(
            scope,
            self.links,
            [
                NewPoint(ids[target], LINK_VECTOR, link_payload(user_id, source_id, target, token=token, now=now))
                for target in targets
            ],
        )
        # insert_only leaves an existing link as it was: the links that carry our token are the new ones
        stored = {point.id: point.payload.get(TOKEN_KEY) for point in await self.store.get(
            scope, self.links, list(ids.values()), with_payload=[TOKEN_KEY], quorum=True,
        )}
        created = [target for target in targets if stored.get(ids[target]) == token]
        logger.info("Memory links created", extra={
            "user_id": str(user_id), "source_id": str(source_id), "links_created": [str(t) for t in created],
        })
        return created

    async def _linkable_targets(self, scope: Scope, source_id: UUID, target_ids: list[UUID]) -> list[UUID]:
        """The distinct targets, in the order given, that exist for the tenant; none unless the source exists too."""
        wanted = [target for target in dict.fromkeys(target_ids) if target != source_id]
        # A majority of the replicas answers: the source was usually written a moment ago, through another node
        owned = {point.id for point in await self.store.get(
            scope, self.memories, [source_id, *wanted], with_payload=False, quorum=True,
        )}
        if source_id not in owned:
            return []
        return [target for target in wanted if target in owned]

    async def unlink_memories(self, user_id: UUID, source_id: UUID, target_id: UUID) -> bool:
        """Remove the link between two memories.

        Returns:
            True if link was removed, False if link didn't exist
        """
        scope = Scope.of(user_id)
        identifier = link_id(user_id, source_id, target_id)
        existed = bool(await self.store.get(scope, self.links, [identifier], with_payload=False))
        if existed:
            await self.store.delete(scope, self.links, [identifier])
        logger.info("Memory link removed", extra={
            "user_id": str(user_id),
            "source_id": str(source_id),
            "target_id": str(target_id),
            "deleted": existed,
        })
        return existed
