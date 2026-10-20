"""Memory Service - Core business logic for memory operations

This service implements the primary functionality for the Axmem Memory System:
    - Semantic search with token budget management
    - Memory creation with auto-linking
    - Memory updates
    - Manual linking between memories
    - Retrieval within a project scope
"""
import asyncio
from datetime import datetime
from uuid import UUID

from app.config.logging_config import logging
from app.config.settings import settings
from app.exceptions import InvalidStateTransitionError
from app.models.memory_models import (
    VALID_SORT_BY,
    VALID_SORT_ORDER,
    LinkedMemory,
    Memory,
    MemoryCreate,
    MemoryQueryRequest,
    MemoryQueryResult,
    MemorySummary,
    MemoryUpdate,
    ObsoleteMatch,
    clean_scope,
)
from app.protocols.memory_protocol import MemoryRepository
from app.utils.provenance import (
    apply_provenance_defaults,
    apply_provenance_defaults_for_update,
)
from app.utils.pydantic_helper import get_changed_fields
from app.utils.token_counter import get_token_counter

logger = logging.getLogger(__name__)


def _normalise_sort_option(value: object, name: str, allowed: tuple[str, ...]) -> str:
    """Strip and lower-case a sort option, raising ValueError when it is not an allowed value."""
    if isinstance(value, str):
        cleaned = value.strip().lower()
        if cleaned in allowed:
            return cleaned
    raise ValueError(f"{name} must be one of: {', '.join(allowed)}")


def _effective_token_budget(requested: int | None) -> int:
    """A request can lower the token budget but never exceed the server-wide limit."""
    if requested is None:
        return settings.MEMORY_TOKEN_BUDGET
    return min(requested, settings.MEMORY_TOKEN_BUDGET)


class MemoryService:
    """Service layer for memory operations

    Handles business logic for creating, updating, querying and linking memories.
    Uses repository protocol for data access.
    """

    def __init__(self, memory_repo: MemoryRepository):
        self.memory_repo = memory_repo
        logger.info("Memory service initialised")

    async def query_memory(
            self,
            user_id: UUID,
            memory_query: MemoryQueryRequest,
    ) -> MemoryQueryResult:
        """Queries memories with token budget managmeent

        Performs two-tier retrieval:
        1. Primary memories from search (top-k)
        2. Linked memories (1-hop neighbors) for each primary result

        Applies token budget limits to ensure results fit within context window.
            
        Args:
            user_id: User ID for isolation
            memory_query: Memory Query Request 

        Returns:
            MemoryQueryResults with primary memories, linked memories, and metadata
        """
        logger.info("querying primary memories", extra={"query": memory_query.query})
        scored = await self.memory_repo.search_scored(
            user_id=user_id,
            query=memory_query.query,
            query_context=memory_query.query_context,
            k=memory_query.k,
            importance_threshold=memory_query.importance_threshold,
            project=memory_query.project,
            exclude_ids=None,
        )
        primary_memories = [m for m, _ in scored]
        score_by_id = {m.id: s for m, s in scored}
        logger.info("primary memory query completed", extra={"number of messages found": len(primary_memories)})

        linked_memories = await self._query_linked_memories(user_id, memory_query, primary_memories)

        logger.info("applying token budget")
        (
            final_primaries,
            final_linked,
            token_count,
            truncated,
        ) = await self._apply_token_budget(
            primary_memories=primary_memories,
            linked_memories=linked_memories,
            max_tokens=_effective_token_budget(memory_query.token_context_threshold),
            max_memories=settings.MEMORY_MAX_MEMORIES,
        )
        logger.info("token budget applied")

        logger.info(
            "returning memories",
            extra={
                "number of primary": len(final_primaries),
                "number of linked": len(final_linked),
                "token_count": token_count,
                "truncated": truncated,
            },
        )

        return MemoryQueryResult(
            query=memory_query.query,
            primary_memories=final_primaries,
            linked_memories=final_linked,
            scores=[score_by_id[m.id] for m in final_primaries if m.id in score_by_id],
            total_count=len(final_primaries) + len(final_linked),
            token_count=token_count,
            truncated=truncated,
        )

    async def _query_linked_memories(
            self,
            user_id: UUID,
            memory_query: MemoryQueryRequest,
            primary_memories: list[Memory],
    ) -> list[LinkedMemory]:
        """Fetch the 1-hop neighbours of the primary results when the request asks for links."""
        if not (memory_query.include_links and memory_query.max_links_per_primary > 0):
            return []

        logger.info("querying linked memories", extra={"number of primary memories": len(primary_memories)})

        linked_memory_project = None
        if memory_query.strict_project_filter:
            linked_memory_project = memory_query.project

        linked_memories = await self._fetch_linked_memories(
            user_id=user_id,
            primary_memories=primary_memories,
            max_links_per_primary=memory_query.max_links_per_primary,
            project=linked_memory_project,
        )
        logger.info("linked memory query completed", extra={"number of linked memories": len(linked_memories)})
        return linked_memories

    async def create_memory(
            self,
            user_id: UUID,
            memory_data: MemoryCreate,
    ) -> tuple[Memory, list[MemorySummary]]:
        """Create a new memory in the system

        Args:
            user_id: User ID
            memory_data: Memory Create object with data to be created
        """
        memory_data = apply_provenance_defaults(memory_data)
        memory = await self.memory_repo.create_memory(
            user_id=user_id,
            memory=memory_data,
        )

        similar_memories = []
        linked_ids = []
        if settings.MEMORY_NUM_AUTO_LINK > 0:
            similar_scored = await self.memory_repo.find_similar_memories_scored(
                memory_id=memory.id,
                user_id=user_id,
                max_links=settings.MEMORY_NUM_AUTO_LINK,
            )

            if similar_scored:
                target_ids = [m.id for m, _ in similar_scored]
                linked_ids = await self.memory_repo.create_links_batch(
                    user_id=user_id,
                    source_id=memory.id,
                    target_ids=target_ids,
                )
                memory.linked_memory_ids = linked_ids

                # Convert to MemorySummary for response
                similar_memories = [
                    MemorySummary(
                        id=m.id,
                        title=m.title,
                        keywords=m.keywords,
                        tags=m.tags,
                        importance=m.importance,
                        created_at=m.created_at,
                        updated_at=m.updated_at,
                        similarity=round(sim, 4),
                    )
                    for m, sim in similar_scored
                ]

                logger.info("Automatically linked memories", extra={
                    "user_id": user_id,
                    "memory_id": memory.id,
                    "number linked": len(target_ids),
                })

        return memory, similar_memories

    async def update_memory(
            self,
            user_id: UUID,
            memory_id: UUID,
            updated_memory: MemoryUpdate,
    ) -> Memory | None:
        """Update an existing memory

        Args:
            user_id: user_id 
            memory_id: memory_id of the memory being updated
            updated_memory: Memory Update object containing the data to be updated
        """
        updated_memory = apply_provenance_defaults_for_update(updated_memory)

        existing_memory = await self.memory_repo.get_memory_by_id(
            memory_id=memory_id,
            user_id=user_id,
        )


        changed_fields = get_changed_fields(
            input_model=updated_memory,
            existing_model=existing_memory,
        )

        if not changed_fields:
            logger.info("no changes detected, returning existing memory",
                        extra={
                            "memory_id": memory_id,
                            "user_id": user_id,
                        })
            return existing_memory

        modified_memory = await self.memory_repo.update_memory(
            memory_id=memory_id,
            user_id=user_id,
            updated_memory=updated_memory,
        )

        return modified_memory

    async def mark_memory_obsolete(
            self,
            user_id: UUID,
            memory_id: UUID,
            reason: str,
            superseded_by: UUID | None = None,
    ) -> bool:
        """Mark a memory as obsolete (soft delete)
        
        Args:
            user_id: User ID
            memory_id: Memory ID to mark obsolete
            reason: Why this memory is obsolete
            superseded_by: Optional ID of memories that have superseded this one

        Returns:
            True if marked obsolete, False if not found

        Raises:
            NotFoundError: If the memory (or the superseding memory) is not found
            InvalidStateTransitionError: If the memory is already obsolete or would
                be superseded by itself
        """
        logger.info("Marking memory as obsolete", extra={
            "memory_id": memory_id,
            "user_id": user_id,
        })

        # Get the memory first to reject an already obsolete one or a self-supersession
        memory = await self.memory_repo.get_memory_by_id(
            memory_id=memory_id,
            user_id=user_id,
        )

        if superseded_by == memory_id:
            raise InvalidStateTransitionError(
                f"Memory {memory_id} cannot be superseded by itself",
            )
        if memory and memory.is_obsolete:
            raise InvalidStateTransitionError(f"Memory {memory_id} is already obsolete")

        success = await self.memory_repo.mark_obsolete(
            memory_id=memory_id,
            user_id=user_id,
            reason=reason,
            superseded_by=superseded_by,
        )

        return success

    async def unobsolete_memory(self, user_id: UUID, memory_id: UUID) -> bool:
        """Restore a soft-deleted memory to active search results."""
        logger.info(
            "Restoring obsolete memory",
            extra={"memory_id": memory_id, "user_id": user_id},
        )
        success = await self.memory_repo.restore_obsolete(
            user_id=user_id,
            memory_id=memory_id,
        )
        if not success:
            # Raises NotFoundError when the memory does not exist for this tenant; otherwise it is simply active
            await self.memory_repo.get_memory_by_id(
                memory_id=memory_id,
                user_id=user_id,
            )
            raise InvalidStateTransitionError(f"Memory {memory_id} is not obsolete")
        return True

    async def get_memory(
            self,
            user_id: UUID,
            memory_id: UUID,
    ) -> Memory | None:
        """Retrieve a single memory by id.

        Args:
            user_id: User Id
            memory_id: Memory id to retrieve

        Returns:
            Memory object or None if not found
        """
        return await self.memory_repo.get_memory_by_id(
            memory_id=memory_id,
            user_id=user_id,
        )

    async def find_obsolete_matches(
            self,
            user_id: UUID,
            memory_id: UUID,
    ) -> list[ObsoleteMatch]:
        if not settings.OBSOLETE_WARNING_ENABLED:
            return []

        try:
            matches = await self.memory_repo.find_obsolete_matches(
                user_id=user_id,
                memory_id=memory_id,
                limit=3,
                min_similarity=settings.OBSOLETE_WARNING_THRESHOLD,
            )
        except Exception as exc:  # noqa: BLE001 - create must not fail on warning lookup
            logger.warning(
                "find_obsolete_matches failed for memory %s: %s",
                memory_id,
                exc,
            )
            return []

        return [
            ObsoleteMatch(
                id=m.id,
                title=m.title,
                similarity=round(sim, 4),
                obsolete_reason=m.obsolete_reason,
                superseded_by=m.superseded_by,
                obsoleted_at=m.obsoleted_at,
                project=m.project,
            )
            for m, sim in matches
        ]

    async def list_memories(
            self,
            user_id: UUID,
            limit: int = 10,
            offset: int = 0,
            project: str | None = None,
            include_obsolete: bool = False,
            sort_by: str = "created_at",
            sort_order: str = "desc",
            tags: list[str] | None = None,
            importance_min: int | None = None,
            created_since: datetime | None = None,
    ) -> tuple[list[Memory], int]:
        """Retrieve memories with pagination, sorting, and filtering.

        Args:
            user_id: User ID
            limit: Maximum number of memories to return
            offset: Skip N results for pagination
            project: Optional filter to only retrieve memories in this project scope
            include_obsolete: Include soft-deleted memories (default False)
            sort_by: Sort field - created_at, updated_at, importance
            sort_order: Sort direction - asc, desc
            tags: Filter by ANY of these tags (OR logic)
            importance_min: Minimum importance score (optional)
            created_since: Inclusive creation timestamp floor (UTC-aware)

        Returns:
            Tuple of (memories, total_count) where total_count is count before pagination

        Raises:
            ValueError: If sort_by or sort_order is not one of the supported values, or the
                project label is blank or too long
        """
        sort_by = _normalise_sort_option(sort_by, "sort_by", VALID_SORT_BY)
        sort_order = _normalise_sort_option(sort_order, "sort_order", VALID_SORT_ORDER)
        project = clean_scope(project)

        return await self.memory_repo.list_memories(
            user_id=user_id,
            limit=limit,
            offset=offset,
            project=project,
            include_obsolete=include_obsolete,
            sort_by=sort_by,
            sort_order=sort_order,
            tags=tags,
            importance_min=importance_min,
            created_since=created_since,
        )

    async def link_memories(
            self,
            user_id: UUID,
            memory_id: UUID,
            related_ids: list[UUID],
    ) -> list[UUID]:
        """Creates a bidirectional links between memories

        Duplicate links and self-links are automatically removed

        Args:
            user_id: User ID for isolation
            memory_id: Source memory id
            related_ids: List of target memory IDs to link

        Returns:
            List of target memory IDs that were successfully linked
        """
        source_memory = await self.memory_repo.get_memory_by_id(
            memory_id=memory_id,
            user_id=user_id,
        )

        links_created = await self.memory_repo.create_links_batch(
            user_id=user_id,
            source_id=source_memory.id,
            target_ids=related_ids,
        )

        return links_created

    async def unlink_memories(
            self,
            user_id: UUID,
            memory_id: UUID,
            target_id: UUID,
    ) -> bool:
        """Remove link between two memories.

        Args:
            user_id: User ID for isolation
            memory_id: Source memory ID
            target_id: Target memory ID to unlink

        Returns:
            True if link was removed, False if link didn't exist

        Raises:
            NotFoundError: If source memory doesn't exist
        """
        # Verify source memory exists (the repository raises NotFoundError if not).
        await self.memory_repo.get_memory_by_id(memory_id=memory_id, user_id=user_id)

        return await self.memory_repo.unlink_memories(
            user_id=user_id,
            source_id=memory_id,
            target_id=target_id,
        )

    async def _fetch_linked_memories(
            self,
            user_id,
            primary_memories: list[Memory],
            max_links_per_primary: int,
            project: str | None,
    ) -> list[LinkedMemory]:
        """Fetch linked memories for each primary result

        Primary results and memories already linked from an earlier primary are excluded
        before the per-primary cap is applied, so they never consume its quota.

        Args:
            user_id: User ID for whom to link the memories for
            primary memories: list of the primary Memories objects to link from
            max_links_per_primary: Maximum links per primary memory
        
        Returns:
            List of LinkedMemory objects
        """
        linked_memories = []
        seen_ids = {m.id for m in primary_memories}

        for primary in primary_memories:
            try:
                links = await self.memory_repo.get_linked_memories(
                    memory_id=primary.id,
                    user_id=user_id,
                    project=project,
                    max_links=max_links_per_primary,
                    exclude_ids=sorted(seen_ids),
                )

                for linked_memory in links:
                    if linked_memory.id in seen_ids:
                        continue

                    linked_memories.append(
                        LinkedMemory(
                            memory=linked_memory,
                            link_source_id=primary.id,
                        ),
                    )

                    seen_ids.add(linked_memory.id)
            except Exception:
                logger.warning(
                    "failed to fetch memories",
                    exc_info=True,
                    extra={
                        "primary_id": primary.id,
                    },
                )

        return linked_memories


    async def _apply_token_budget(
            self,
            primary_memories: list[Memory],
            linked_memories: list[LinkedMemory],
            max_tokens: int,
            max_memories: int,
    ) -> tuple[list[Memory], list[LinkedMemory], int, bool]:
        """Apply token budget and count limits to memory results

        Strategy: 
        1. Prioritise primary memories (sorted by importance)
        2. Add linked memories if space remains
        3. Enforce hard limit of max_total_count memories

        Args:
            primary_memories: List of Memory objects of the primary memories
            linked_memories: List of LinkedMemory objects of the linked memories
            max_tokens: maximum total tokens allowed
            max_memories: the maximum number of Memory objects to return

        Returns:
            Tuple of (list primary memories, list linked memories, token count and was truncated)
        """
        truncated_primary, primary_tokens, primary_truncated = await self.truncate_memories_by_budget(
            memories=primary_memories,
            max_tokens=max_tokens,
            max_count=max_memories,
        )

        remaining_tokens = max_tokens - primary_tokens
        remaining_count = max_memories - len(truncated_primary)

        if primary_truncated:
            return truncated_primary, [], primary_tokens, True

        truncated_linked = []
        linked_tokens = 0
        linked_truncated = False

        if linked_memories:
            linked_memory_objects = [lm.memory for lm in linked_memories]
            truncated_memory_objects, linked_tokens, linked_truncated = await self.truncate_memories_by_budget(
                memories=linked_memory_objects,
                max_tokens=remaining_tokens,
                max_count=remaining_count,
            )

            truncated_ids = {m.id for m in truncated_memory_objects}
            truncated_linked = [lm for lm in linked_memories if lm.memory.id in truncated_ids]

        total_tokens = primary_tokens + linked_tokens

        return truncated_primary, truncated_linked, total_tokens, linked_truncated

    def _count_memory_tokens(self, memory: Memory) -> int:
        """Count total tokens for a memory

        Args:
            Memory object to count tokens for

        Returns:
            token count
        """
        text_parts = [
            memory.title,
            memory.content,
            memory.context,
            " ".join(memory.keywords),
            " ".join(memory.tags),
        ]

        total_text = " ".join(text_parts)

        return get_token_counter().count_tokens(total_text)

    async def truncate_memories_by_budget(
            self,
            memories: list[Memory],
            max_tokens: int,
            max_count: int,
    ) -> tuple[list[Memory], int, bool]:
        """Truncate memory list to fit within token budget

        Prioritises by importance score (higher = kept first)

        Args:
            memories: List of Memory Objects
            max_tokens: Maximum allowed tokens
            max_count: Maximum number of memories to return

        Returns:
            Tuple of (truncated memories, actual_token_count, was_truncated), where
            was_truncated is set when memories were dropped by either the token budget
            or the max_count limit
        """
        if not memories:
            return [], 0, False

        # Loading the tokenizer can download it, keep that off the event loop
        await asyncio.to_thread(get_token_counter)

        sorted_memories = sorted(
            memories,
            key=lambda m: m.importance,
            reverse=True,
        )

        count_truncated = len(sorted_memories) > max_count
        sorted_memories = sorted_memories[:max_count]

        selected: list[Memory] = []
        running_total = 0

        for memory in sorted_memories:
            memory_tokens = self._count_memory_tokens(memory=memory)

            if running_total + memory_tokens > max_tokens:
                logger.info(
                    "Truncated returned memories",
                    extra={
                        "returned memories": len(selected),
                        "skipped memories": len(memories) - len(selected),
                        "total returned tokens": running_total,
                    },
                )

                return selected, running_total, True

            selected.append(memory)
            running_total += memory_tokens

        return selected, running_total, count_truncated
