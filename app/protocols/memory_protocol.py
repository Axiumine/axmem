from dataclasses import dataclass
from datetime import datetime
from typing import Protocol
from uuid import UUID

from app.models.memory_models import Memory, MemoryCreate, MemoryScore, MemoryUpdate


@dataclass
class ValidationResult:
    """Result of post-re-embedding validation checks"""
    count_ok: bool
    dimensions_ok: bool
    search_ok: bool

    @property
    def all_passed(self) -> bool:
        return self.count_ok and self.dimensions_ok and self.search_ok


class MemoryRepository(Protocol):
    """Contract for the Memory Repository"""

    async def search(
            self,
            user_id: UUID,
            query: str,
            query_context: str,
            k: int,
            importance_threshold: int | None,
            project: str | None,
            exclude_ids: list[int] | None,
    ) -> list[Memory]:
        ...

    async def search_scored(
            self,
            user_id: UUID,
            query: str,
            query_context: str,
            k: int,
            importance_threshold: int | None,
            project: str | None,
            exclude_ids: list[int] | None,
    ) -> list[tuple[Memory, MemoryScore]]:
        ...

    async def create_memory(
            self,
            user_id: UUID,
            memory: MemoryCreate,
    ) -> Memory:
        ...
    async def create_links_batch(
            self,
            user_id: UUID,
            source_id: int,
            target_ids: list[int],
    ) -> list[int]:
        ...
    async def get_memory_by_id(
            self,
            user_id: UUID,
            memory_id: int,
    ) -> Memory:
        ...
    async def update_memory(
            self,
            user_id: UUID,
            memory_id: int,
            updated_memory: MemoryUpdate,
            existing_memory: Memory,
            search_fields_changed: bool,
    ) -> Memory | None:
        ...
    async def mark_obsolete(
            self,
            user_id: UUID,
            memory_id: int,
            reason: str,
            superseded_by: int | None = None,
    ) -> bool:
        ...
    async def restore_obsolete(
            self,
            user_id: UUID,
            memory_id: int,
    ) -> bool:
        ...
    async def get_linked_memories(
            self,
            user_id: UUID,
            memory_id: int,
            project: str | None,
            max_links: int = 5,
            exclude_ids: list[int] | None = None,
    ) -> list[Memory]:
        ...
    async def find_similar_memories(
            self,
            user_id: UUID,
            memory_id: int,
            max_links: int,
    ) -> list[Memory]:
        ...

    async def find_similar_memories_scored(
            self,
            user_id: UUID,
            memory_id: int,
            max_links: int,
    ) -> list[tuple[Memory, float]]:
        ...

    async def find_obsolete_matches(
            self,
            user_id: UUID,
            memory_id: int,
            limit: int = 3,
            min_similarity: float = 0.89,
    ) -> list[tuple[Memory, float]]:
        ...

    async def list_memories(
            self,
            user_id: UUID,
            limit: int,
            offset: int = 0,
            project: str | None = None,
            include_obsolete: bool = False,
            sort_by: str = "created_at",
            sort_order: str = "desc",
            tags: list[str] | None = None,
            importance_min: int | None = None,
            created_since: datetime | None = None,
    ) -> tuple[list[Memory], int]:
        """Get memories with pagination, sorting, and filtering.

        Args:
            user_id: User ID
            limit: Max results to return
            offset: Skip N results for pagination
            project: Filter by project scope (optional)
            include_obsolete: Include soft-deleted memories
            sort_by: Sort field - created_at, updated_at, importance
            sort_order: Sort direction - asc, desc
            tags: Filter by ANY of these tags (OR logic)
            importance_min: Minimum importance score (optional)
            created_since: Inclusive creation timestamp floor (UTC-aware)

        Returns:
            Tuple of (memories, total_count) where total_count is
            the count BEFORE limit/offset applied (for pagination)

        Raises:
            ValueError: If sort_by or sort_order is not a supported (lower-case) value
        """
        ...

    async def unlink_memories(
            self,
            user_id: UUID,
            source_id: int,
            target_id: int,
    ) -> bool:
        """Remove bidirectional link between two memories.

        Args:
            user_id: User ID for isolation
            source_id: Source memory ID
            target_id: Target memory ID to unlink

        Returns:
            True if link was removed, False if link didn't exist
        """
        ...

    # Re-embedding support methods

    async def count_all_memories(self) -> int:
        """Count all memories, including obsolete ones, across all users."""
        ...

    async def get_memories_for_reembedding(self, limit: int, offset: int) -> list[Memory]:
        """Fetch all memories, including obsolete ones, in batches ordered by id."""
        ...

    async def count_memories_for_targeted_rebuild(
            self,
            user_id: UUID,
            memory_ids: list[int] | None = None,
            project: str | None = None,
    ) -> int:
        """Count memories eligible for a *user-scoped, targeted* embedding rebuild.

        Used by `rebuild_embeddings` to avoid the destructive global
        `re_embed_all` path. Filters are user-scoped; user_id is mandatory and
        scopes ownership. Matching memories are rebuilt unconditionally.

        Args:
            user_id: Authenticated user id; ownership filter, never optional.
            memory_ids: Restrict to explicit ids (already user-scoped server-side).
            project: Restrict to one project scope of `user_id`.

        Returns:
            Number of non-obsolete memories matching the filters and owned by
            `user_id`.
        """
        ...

    async def get_memories_for_targeted_rebuild(
            self,
            user_id: UUID,
            limit: int,
            after_id: int | None = None,
            memory_ids: list[int] | None = None,
            project: str | None = None,
    ) -> list[Memory]:
        """Page through memories selected by `count_memories_for_targeted_rebuild`.

        Same semantics and same filters as the count variant. Uses keyset
        pagination by memory id so callers can process deterministically.
        """
        ...

    async def upsert_targeted_embeddings(
            self,
            user_id: UUID,
            updates: list[tuple[int, list[float]]],
            expected_updated_at: dict[int, datetime] | None = None,
    ) -> list[int]:
        """Idempotently write a batch of new embeddings for `user_id`.

        Unlike `bulk_update_embeddings`, this method:
        - is user-scoped (rejects ids not owned by `user_id`),
        - never resets vector storage,
        - is idempotent for repeated calls on the same memory id,
        - MUST NOT mutate `updated_at` (an embedding refresh is not an edit).

        Args:
            user_id: Authenticated user id; ownership filter, never optional.
            updates: list of (memory_id, embedding) tuples.
            expected_updated_at: Optional memory id -> `updated_at` observed when the
                embedding input was read. A memory whose `updated_at` no longer matches
                was edited (and re-embedded) meanwhile: its stored vector is left alone
                and its id is not returned. Ids without an entry are written unconditionally.
                Implementations MUST make the check part of the write itself, so an edit
                that commits after the check cannot be overwritten.

        Returns:
            list of memory ids whose embedding was actually written.
        """
        ...

    async def project_exists_for_user(self, user_id: UUID, project: str) -> bool:
        """Return True only if `user_id` has used the project scope `project` before."""
        ...

    async def reset_embedding_storage(self) -> None:
        """Prepare vector storage for new dimensions.
        SQLite: DROP + CREATE vec_memories with new dimensions
        Postgres: ALTER embedding column type
        """
        ...

    async def finalize_embedding_storage(self) -> None:
        """Undo what `reset_embedding_storage` relaxed, once every memory has a new embedding.
        SQLite: nothing to restore
        Postgres: restore NOT NULL on the embedding column
        """
        ...

    async def bulk_update_embeddings(self, updates: list[tuple[int, list[float]]]) -> None:
        """Write new embeddings for a batch of memory IDs. MUST NOT mutate `updated_at`."""
        ...

    async def validate_embedding_count(self) -> bool:
        """Check embedding count matches all memories, including obsolete ones."""
        ...

    async def validate_embedding_dimensions(self) -> bool:
        """Sample embeddings and verify correct dimensions"""
        ...

    async def validate_search_works(self) -> bool:
        """Smoke-test vector storage, including obsolete memories."""
        ...
