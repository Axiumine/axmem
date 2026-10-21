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


@dataclass
class EmbeddingWrites:
    """Outcome of writing a batch of embeddings: which memories took theirs and which could not be written."""
    written: list[UUID]
    failed: list[UUID]


@dataclass(frozen=True)
class StoredMemory:
    """A memory as stored: the memory, its tenant, and the version its record had when it was read.

    The version is what makes a later write conditional: a write that carries it is applied only while
    the record still has that version, so an embedding computed from this read can never replace the
    result of a newer edit.
    """

    memory: Memory
    tenant_id: UUID
    version: int


class MemoryRepository(Protocol):
    """Contract for the Memory Repository

    Every method that takes ``user_id`` (the tenant) reads and writes that tenant's memories only.
    The methods that do not are operator operations over every tenant (re-embedding).
    """

    async def search(
            self,
            user_id: UUID,
            query: str,
            query_context: str,
            k: int,
            importance_threshold: int | None,
            project: str | None,
            exclude_ids: list[UUID] | None,
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
            exclude_ids: list[UUID] | None,
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
            source_id: UUID,
            target_ids: list[UUID],
    ) -> list[UUID]:
        """Link the source to each target that exists and is not linked yet; return the targets newly linked."""
        ...
    async def get_memory_by_id(
            self,
            user_id: UUID,
            memory_id: UUID,
    ) -> Memory:
        ...
    async def update_memory(
            self,
            user_id: UUID,
            memory_id: UUID,
            updated_memory: MemoryUpdate,
    ) -> Memory:
        """Apply the changes to the memory as one conditional write.

        A write that loses to a concurrent change is repeated against the new state, up to a bounded
        number of times.

        Raises:
            NotFoundError: If the memory does not exist for this tenant
            ConflictError: If concurrent changes kept the write from being applied
        """
        ...
    async def mark_obsolete(
            self,
            user_id: UUID,
            memory_id: UUID,
            reason: str,
            superseded_by: UUID | None = None,
    ) -> bool:
        ...
    async def restore_obsolete(
            self,
            user_id: UUID,
            memory_id: UUID,
    ) -> bool:
        ...
    async def get_linked_memories(
            self,
            user_id: UUID,
            memory_id: UUID,
            project: str | None,
            max_links: int = 5,
            exclude_ids: list[UUID] | None = None,
    ) -> list[Memory]:
        ...
    async def find_similar_memories(
            self,
            user_id: UUID,
            memory_id: UUID,
            max_links: int,
    ) -> list[Memory]:
        ...

    async def find_similar_memories_scored(
            self,
            user_id: UUID,
            memory_id: UUID,
            max_links: int,
    ) -> list[tuple[Memory, float]]:
        ...

    async def find_obsolete_matches(
            self,
            user_id: UUID,
            memory_id: UUID,
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
            source_id: UUID,
            target_id: UUID,
    ) -> bool:
        """Remove the link between two memories.

        Args:
            user_id: User ID for isolation
            source_id: Source memory ID
            target_id: Target memory ID to unlink

        Returns:
            True if link was removed, False if link didn't exist
        """
        ...

    # Targeted, tenant-scoped rebuild of embeddings

    async def count_memories_for_targeted_rebuild(
            self,
            user_id: UUID,
            memory_ids: list[UUID] | None = None,
            project: str | None = None,
    ) -> int:
        """Count memories eligible for a *tenant-scoped, targeted* embedding rebuild.

        Used by `rebuild_embeddings`; the tenant scopes ownership and is never optional.

        Args:
            user_id: Authenticated tenant; ownership filter, never optional.
            memory_ids: Restrict to explicit ids (already tenant-scoped server-side).
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
            after_id: UUID | None = None,
            memory_ids: list[UUID] | None = None,
            project: str | None = None,
    ) -> list[StoredMemory]:
        """Page through memories selected by `count_memories_for_targeted_rebuild`.

        Same semantics and same filters as the count variant. Uses keyset
        pagination by memory id so callers can process deterministically.
        """
        ...

    async def upsert_targeted_embeddings(
            self,
            user_id: UUID,
            updates: list[tuple[UUID, list[float]]],
            expected_versions: dict[UUID, int],
    ) -> EmbeddingWrites:
        """Idempotently write a batch of new embeddings for `user_id`.

        Each write is conditional on the version the embedding input was read at
        (`StoredMemory.version`): a memory edited (and re-embedded) meanwhile keeps
        its vector and is in neither list of the result. The condition is part of the
        write itself, so an edit that commits after the check cannot be overwritten.
        A memory whose write fails is listed as failed and does not stop the others.
        """
        ...

    async def project_exists_for_user(self, user_id: UUID, project: str) -> bool:
        """Return True only if `user_id` has used the project scope `project` before."""
        ...

    # Re-embedding of every memory into a new generation of the memory collection (operator, all tenants)

    async def count_all_memories(self) -> int:
        """Count all memories, including obsolete ones, across all tenants."""
        ...

    async def live_generation(self) -> str:
        """Name of the generation (collection) that serves reads and writes now."""
        ...

    async def create_generation(self) -> str:
        """Create an empty generation with the configured embedding dimensions; return its name."""
        ...

    async def outdated_in(
            self,
            source: str,
            target: str,
            limit: int,
            start: UUID | None,
    ) -> tuple[list[StoredMemory], UUID | None]:
        """Scan up to `limit` memories of `source` from the id `start`; return those `target` lacks or holds an older version of.

        The second value is the id to continue from, or None when the scan reached the end of `source`. A scan is complete
        or it fails: a memory that is being rewritten is waited for, never left out.
        """
        ...

    async def copy_into(self, target: str, items: list[tuple[StoredMemory, list[float]]]) -> int:
        """Write the memories with their new embeddings into `target`; return how many of them `target` holds afterwards.

        A memory `target` does not have is created, one it has at an older version is replaced, and one it
        has at the same or a newer version is left alone, so a copy can never overwrite a newer edit. The count is
        read back from `target`: a memory counts when it is there at the version written or a newer one.
        """
        ...

    async def activate_generation(self, generation: str) -> None:
        """Make `generation` the live one, atomically."""
        ...

    async def discard_generation(self, generation: str) -> None:
        """Delete a generation that was never activated."""
        ...

    async def rebuild_links(self, *, unfinished_only: bool = False) -> bool:
        """Give the stored links the layout the settings describe (shards, replicas, write consistency); return
        whether they had to be rebuilt. A run that stopped half way is finished by running it again;
        ``unfinished_only`` does nothing else than finish such a run."""
        ...

    async def validate_generation(self, generation: str) -> ValidationResult:
        """Check the memory count, the vector width and a similarity search of a generation."""
        ...
