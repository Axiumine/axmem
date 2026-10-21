"""Re-embedding service: new embeddings for every memory, or for a tenant's subset.

Orchestrates the workflow of embedding all memories again with the currently configured
provider, and the tenant-scoped rebuild. Knows nothing about Qdrant specifics.
"""
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from uuid import UUID

from app.config.settings import settings
from app.exceptions import EmbeddingUnavailableError
from app.models.memory_models import clean_scope
from app.protocols.embedding_protocol import EmbeddingsAdapter
from app.protocols.memory_protocol import (
    MemoryRepository,
    StoredMemory,
    ValidationResult,
)
from app.utils.embedding_helpers import build_embedding_text

logger = logging.getLogger(__name__)

# Scans of the old generation made to pick up edits that landed while the copy ran, before giving up
MAX_CATCH_UP_PASSES = 5


@dataclass
class ReEmbedResult:
    """Result of a re-embedding operation"""
    total_processed: int
    total_memories: int
    validation: ValidationResult | None = None
    # The generation that serves now (None when validation failed and nothing was switched)
    generation: str | None = None
    # The generation that served before; it keeps the old vectors until it is deleted
    previous_generation: str | None = None
    # Whether the stored links had to be rebuilt in the layout the settings describe
    links_rebuilt: bool = False


@dataclass
class TargetedRebuildResult:
    """Outcome of a tenant-scoped, targeted `rebuild_embeddings` call.

    Distinguishes successful rebuilds from skipped (not owned / no-op) and
    failed (embedding generation or write error) memories so callers can
    surface partial success without re-running the whole rebuild.

    `failed[].reason` is a stable machine-readable code (`embedding_failed`,
    `persistence_failed`, `auto_link_failed`, `not_found_or_not_owned`); exception
    details go to the server log only, never to the caller.
    """
    rebuilt_ids: list[UUID] = field(default_factory=list)
    skipped_ids: list[UUID] = field(default_factory=list)
    failed: list[dict] = field(default_factory=list)


class ReEmbeddingService:
    """Orchestrates batch re-embedding of all memories"""

    def __init__(
        self,
        memory_repository: MemoryRepository,
        embedding_adapter: EmbeddingsAdapter,
        batch_size: int = 20,
    ):
        if batch_size < 1:
            raise ValueError(f"batch_size must be at least 1, got {batch_size}")
        self.memory_repository = memory_repository
        self.embedding_adapter = embedding_adapter
        self.batch_size = batch_size
        # The generation whose activation the current ``re_embed_all`` has begun. Once set, a failure no longer
        # leaves the live collection as it was (the alias may already point at it), so the caller must not say so.
        self.switch_started: str | None = None

    async def re_embed_all(
        self,
        progress_callback: Callable[[int, int], None] | None = None,
    ) -> ReEmbedResult:
        """Copy every memory, embedded again, into a new generation and make that generation live.

        The live generation keeps serving, and nothing in it is changed, until the new one is complete
        and valid: a failure or an interruption discards the new generation and leaves the old one as
        it was. Edits made while the copy runs are picked up by further scans of the old generation, and
        once more after the switch, for writes that were still in flight when it happened. Every write
        into the new generation is conditional on the version, so none can overwrite a newer edit.

        Args:
            progress_callback: Optional callback(processed, total) called after each batch

        Returns:
            ReEmbedResult with counts, the validation outcome and the names of the generations
        """
        self.switch_started = None
        total = await self.memory_repository.count_all_memories()
        source = await self.memory_repository.live_generation()
        target = await self.memory_repository.create_generation()
        logger.info("Starting re-embedding", extra={"total_memories": total, "source": source, "target": target})

        try:
            processed = await self._copy_all(source, target, progress_callback, total)
            for _ in range(MAX_CATCH_UP_PASSES):
                if await self._copy_all(source, target) == 0:
                    break
            validation = await self.memory_repository.validate_generation(target)
        except BaseException:
            await self._discard_keeping_the_cause(target)
            raise
        if not validation.all_passed:
            logger.error("Validation failed", extra={
                "count_ok": validation.count_ok,
                "dimensions_ok": validation.dimensions_ok,
                "search_ok": validation.search_ok,
            })
            await self._discard_keeping_the_cause(target)
            return ReEmbedResult(total_processed=processed, total_memories=total, validation=validation)

        self.switch_started = target
        await self.memory_repository.activate_generation(target)
        for _ in range(MAX_CATCH_UP_PASSES):
            if await self._copy_all(source, target) == 0:
                break
        links_rebuilt = await self.memory_repository.rebuild_links()
        logger.info("Validation passed and generation activated", extra={"generation": target})
        return ReEmbedResult(
            total_processed=processed,
            total_memories=total,
            validation=validation,
            generation=target,
            previous_generation=source,
            links_rebuilt=links_rebuilt,
        )

    async def _discard_keeping_the_cause(self, generation: str) -> None:
        """Delete the unfinished generation of a run that failed, without letting a failure to delete replace the cause
        (the error being raised, or the validation report being returned)."""
        try:
            await self.memory_repository.discard_generation(generation)
        except Exception:
            logger.exception("The unfinished generation could not be deleted", extra={"generation": generation})

    async def _copy_all(
        self,
        source: str,
        target: str,
        progress_callback: Callable[[int, int], None] | None = None,
        total: int = 0,
    ) -> int:
        """Scan the whole of ``source`` and copy what ``target`` lacks or holds an older version of; return how many."""
        copied = 0
        start: UUID | None = None
        while True:
            outdated, start = await self.memory_repository.outdated_in(source, target, self.batch_size, start)
            if outdated:
                embeddings = await self.embedding_adapter.generate_embeddings(
                    [build_embedding_text(stored.memory) for stored in outdated],
                )
                copied += await self.memory_repository.copy_into(
                    target, list(zip(outdated, embeddings, strict=True)),
                )
                if progress_callback:
                    progress_callback(copied, total)
            if start is None:
                return copied

    async def validate_targeted_scope(
        self,
        user_id: UUID,
        memory_ids: list[UUID] | None,
        project: str | None,
    ) -> None:
        """Validate rebuild scope before running embedding work."""
        project = clean_scope(project)
        if memory_ids is not None and len(memory_ids) == 0:
            raise ValueError(
                "memory_ids must be non-empty if provided; "
                "pass null for global scope",
            )

        if memory_ids is None:
            if project is not None and not await self.memory_repository.project_exists_for_user(
                user_id=user_id,
                project=project,
            ):
                # One message for missing and foreign projects: do not reveal which exist.
                raise ValueError(f"project={project!r} not found")
            return

        if project is None:
            return

        requested_count = len(set(memory_ids))
        repo = self.memory_repository
        count_in_project = await repo.count_memories_for_targeted_rebuild(
            user_id=user_id,
            memory_ids=memory_ids,
            project=project,
        )
        count_without_project = await repo.count_memories_for_targeted_rebuild(
            user_id=user_id,
            memory_ids=memory_ids,
        )
        if count_in_project != count_without_project or count_without_project != requested_count:
            raise ValueError(
                "memory_ids do not all belong to "
                f"project={project!r} (or some are not owned/obsolete). "
                f"expected={requested_count}, matched_in_project={count_in_project}, "
                f"owned_total={count_without_project}",
            )

    async def rebuild_targeted(
        self,
        user_id: UUID,
        memory_ids: list[UUID] | None = None,
        project: str | None = None,
        progress_callback: Callable[[int, int], None] | None = None,
    ) -> TargetedRebuildResult:
        """Tenant-scoped, *non-destructive* embedding rebuild for a memory subset.

        Unlike `re_embed_all`, this method:
        - never creates or switches a generation,
        - operates only on memories owned by `user_id`,
        - reuses the existing embedding pipeline (`build_embedding_text` +
          `EmbeddingsAdapter.generate_embeddings`) so embeddings stay
          dimensionally consistent with the rest of the corpus.

        Args:
            user_id: Authenticated tenant; ownership filter, never optional.
            memory_ids: Restrict to explicit memory ids (already tenant-scoped).
            project: Restrict to one project scope of `user_id`.
            progress_callback: Optional `(processed, total)` callback per batch.

        Returns:
            `TargetedRebuildResult` with per-memory outcome lists.
        """
        project = clean_scope(project)
        total = await self.memory_repository.count_memories_for_targeted_rebuild(
            user_id=user_id,
            memory_ids=memory_ids,
            project=project,
        )
        result = TargetedRebuildResult()
        if total == 0:
            self._record_unresolved_memory_ids(
                memory_ids=memory_ids,
                project=project,
                result=result,
            )
            logger.info(
                "rebuild_embeddings: no candidates",
                extra={
                    "user_id": str(user_id),
                    "memory_ids": [str(memory_id) for memory_id in memory_ids or []],
                    "project": project,
                },
            )
            return result

        processed = 0
        last_seen_id: UUID | None = None
        while True:
            batch = await self.memory_repository.get_memories_for_targeted_rebuild(
                user_id=user_id,
                limit=self.batch_size,
                after_id=last_seen_id,
                memory_ids=memory_ids,
                project=project,
            )
            if not batch:
                break

            await self._rebuild_batch(user_id=user_id, batch=batch, result=result)

            processed += len(batch)
            if progress_callback:
                progress_callback(processed, total)
            last_seen_id = batch[-1].memory.id

        self._record_unresolved_memory_ids(
            memory_ids=memory_ids,
            project=project,
            result=result,
        )
        logger.info(
            "rebuild_embeddings: done",
            extra={
                "user_id": str(user_id),
                "rebuilt": len(result.rebuilt_ids),
                "skipped": len(result.skipped_ids),
                "failed": len(result.failed),
                "total_candidates": total,
            },
        )
        return result

    async def _rebuild_batch(
        self,
        user_id: UUID,
        batch: list[StoredMemory],
        result: TargetedRebuildResult,
    ) -> None:
        """Embed one batch, persist it and refresh auto-links, recording outcomes in `result`."""
        embedded = await self._embed_batch(user_id=user_id, batch=batch, result=result)
        if not embedded:
            return

        writes = await self.memory_repository.upsert_targeted_embeddings(
            user_id=user_id,
            updates=[(stored.memory.id, embedding) for stored, embedding in embedded],
            expected_versions={stored.memory.id: stored.version for stored, _ in embedded},
        )
        failed = set(writes.failed)
        result.rebuilt_ids.extend(writes.written)
        for stored, _ in embedded:
            memory_id = stored.memory.id
            if memory_id in failed:
                result.failed.append({"memory_id": memory_id, "reason": "persistence_failed"})
            elif memory_id not in writes.written:
                result.skipped_ids.append(memory_id)
        await self._recompute_auto_links(user_id=user_id, memory_ids=writes.written, result=result)

    async def _embed_batch(
        self,
        user_id: UUID,
        batch: list[StoredMemory],
        result: TargetedRebuildResult,
    ) -> list[tuple[StoredMemory, list[float]]]:
        """Generate embeddings for one batch; a memory that fails is recorded in `result.failed`.

        The batch goes to the provider in one call. When that call fails, each memory is
        retried on its own, so one memory the provider rejects does not fail the others. A
        provider that says it cannot serve any call (``EmbeddingUnavailableError``) is not asked again
        for every memory: the whole batch is recorded as failed.
        """
        try:
            embeddings = await self.embedding_adapter.generate_embeddings(
                [build_embedding_text(stored.memory) for stored in batch],
            )
            return list(zip(batch, embeddings, strict=True))
        except EmbeddingUnavailableError:
            logger.exception("rebuild_embeddings: the embeddings provider is unavailable, not retrying", extra={
                "user_id": str(user_id),
                "batch_size": len(batch),
            })
            result.failed.extend({"memory_id": stored.memory.id, "reason": "embedding_failed"} for stored in batch)
            return []
        except Exception:  # noqa: BLE001 - intentional broad capture
            logger.exception("rebuild_embeddings: batch embedding failed, retrying one memory at a time", extra={
                "user_id": str(user_id),
                "batch_size": len(batch),
            })
        embedded: list[tuple[StoredMemory, list[float]]] = []
        for stored in batch:
            try:
                embedding = await self.embedding_adapter.generate_embedding(build_embedding_text(stored.memory))
                embedded.append((stored, embedding))
            except Exception:  # noqa: BLE001 - intentional broad capture
                logger.exception("rebuild_embeddings: embedding failed", extra={
                    "memory_id": str(stored.memory.id),
                    "user_id": str(user_id),
                })
                result.failed.append({"memory_id": stored.memory.id, "reason": "embedding_failed"})
        return embedded

    def _record_unresolved_memory_ids(
        self,
        memory_ids: list[UUID] | None,
        project: str | None,
        result: TargetedRebuildResult,
    ) -> None:
        """Surface explicit IDs hidden by tenant/obsolete filters as failures."""
        if memory_ids is None or project is not None:
            return

        resolved_ids = set(result.rebuilt_ids) | set(result.skipped_ids)
        resolved_ids.update(
            failure["memory_id"]
            for failure in result.failed
            if "memory_id" in failure
        )
        for memory_id in dict.fromkeys(memory_ids):
            if memory_id not in resolved_ids:
                result.failed.append({
                    "memory_id": memory_id,
                    "reason": "not_found_or_not_owned",
                })

    async def _recompute_auto_links(
        self,
        user_id: UUID,
        memory_ids: list[UUID],
        result: TargetedRebuildResult,
    ) -> None:
        """Refresh additive auto-links after new vectors have been written."""
        if settings.MEMORY_NUM_AUTO_LINK <= 0:
            return

        for memory_id in memory_ids:
            try:
                similar_memories = await self.memory_repository.find_similar_memories(
                    user_id=user_id,
                    memory_id=memory_id,
                    max_links=settings.MEMORY_NUM_AUTO_LINK,
                )
                if not similar_memories:
                    continue
                await self.memory_repository.create_links_batch(
                    user_id=user_id,
                    source_id=memory_id,
                    target_ids=[memory.id for memory in similar_memories],
                )
            except Exception:  # noqa: BLE001 - link failures are per-memory partial failures
                logger.exception("rebuild_embeddings: auto-link failed", extra={
                    "memory_id": str(memory_id),
                    "user_id": str(user_id),
                })
                result.failed.append({
                    "memory_id": memory_id,
                    "phase": "auto_link",
                    "reason": "auto_link_failed",
                })
