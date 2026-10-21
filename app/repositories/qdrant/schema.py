"""Layout of the Qdrant collections: names, vector configuration, payload indexes and generations.

The live memory collection is an alias, ``<prefix>_memories``, onto one *generation*, a collection named
``<prefix>_memories_<timestamp>``. Re-embedding fills a new generation and then moves the alias, so the
live name never changes. The links live in a collection of their own, ``<prefix>_links``.
"""
import contextlib
import logging
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from qdrant_client import AsyncQdrantClient
from qdrant_client import models as qm
from qdrant_client.http.exceptions import ResponseHandlingException, UnexpectedResponse

from app.config.settings import EMBEDDING_PROVIDERS, canonical_choice, settings
from app.exceptions import QdrantStartupError
from app.repositories.qdrant.client import NodePool, describe_request_failure

logger = logging.getLogger(__name__)

# Payload fields of a memory point that filters, orderings or conditional writes read. Qdrant scans an
# unindexed field, and a condition on a value of the wrong type silently matches nothing, so every field a
# filter names has an index and is always written with its exact type.
TENANT_INDEX = qm.KeywordIndexParams(type=qm.KeywordIndexType.KEYWORD, is_tenant=True)
MEMORY_INDEXES: dict[str, qm.KeywordIndexParams | qm.PayloadSchemaType] = {
    "tenant_id": TENANT_INDEX,
    "project": qm.PayloadSchemaType.KEYWORD,
    "tags": qm.PayloadSchemaType.KEYWORD,
    "is_obsolete": qm.PayloadSchemaType.BOOL,
    "version": qm.PayloadSchemaType.INTEGER,
    "importance": qm.PayloadSchemaType.INTEGER,
    "created_at": qm.PayloadSchemaType.DATETIME,
    "updated_at": qm.PayloadSchemaType.DATETIME,
}
LINK_INDEXES: dict[str, qm.KeywordIndexParams | qm.PayloadSchemaType] = {
    "tenant_id": TENANT_INDEX,
    "a": qm.PayloadSchemaType.KEYWORD,
    "b": qm.PayloadSchemaType.KEYWORD,
}

# The metadata key under which a memory generation records what produced its vectors
EMBEDDER_KEY = "axmem_embedder"

# Link points carry no vector worth searching; Qdrant still wants one per point, so each holds a single
# number and the collection builds no HNSW graph for them.
LINK_VECTOR = [0.0]


def embedder_identity(dimensions: int) -> dict[str, Any]:
    """What produced (or will produce) the vectors of a memory generation, as the settings describe it now.

    These are the settings whose change makes stored vectors incomparable with new ones, the ones the migration guide
    lists: the provider, the model, the width, the precision of the local engine and the backend behind a gateway.
    """
    provider = canonical_choice("EMBEDDING_PROVIDER", settings.EMBEDDING_PROVIDER, EMBEDDING_PROVIDERS)
    identity: dict[str, Any] = {"provider": provider, "model": settings.EMBEDDING_MODEL, "dimensions": dimensions}
    if provider == "TensorRT":
        identity["precision"] = "tf32" if settings.EMBEDDING_TF32 else "fp32"
    if provider == "gateway":
        identity["gateway"] = f"{settings.GATEWAY_BACKEND}/{settings.GATEWAY_MODEL}"
    return identity


def _describe_identity(identity: Any) -> str:
    if not isinstance(identity, dict):
        return "an unreadable record"
    return ", ".join(f"{key} {value}" for key, value in identity.items())


@dataclass(frozen=True)
class CollectionNames:
    """The names this deployment uses, derived from ``QDRANT_COLLECTION_PREFIX``."""

    prefix: str

    @property
    def live(self) -> str:
        """The alias that serves memories now."""
        return f"{self.prefix}_memories"

    @property
    def links(self) -> str:
        return f"{self.prefix}_links"

    def new_generation(self) -> str:
        stamp = datetime.now(UTC).strftime("%Y%m%dt%H%M%S")
        return f"{self.live}_{stamp}{uuid.uuid4().hex[:4]}"


def _layout_problems(params: qm.CollectionParams) -> list[str]:
    """How the shard count, the replication factor and the write consistency factor of a collection differ from the settings."""
    return [
        f"{label} is {found} but QDRANT_{label.upper()} is {wanted}"
        for label, found, wanted in (
            ("shard_number", params.shard_number, settings.QDRANT_SHARD_NUMBER),
            ("replication_factor", params.replication_factor, settings.QDRANT_REPLICATION_FACTOR),
            ("write_consistency_factor", params.write_consistency_factor, settings.QDRANT_WRITE_CONSISTENCY_FACTOR),
        )
        if found != wanted
    ]


def _index_type(schema: qm.PayloadIndexInfo) -> str:
    return str(schema.data_type.value)


def _expected_index_type(spec: qm.KeywordIndexParams | qm.PayloadSchemaType) -> str:
    if isinstance(spec, qm.KeywordIndexParams):
        return str(qm.PayloadSchemaType.KEYWORD.value)
    return str(spec.value)


class QdrantSchema:
    """Creates, verifies and switches the collections. These are operator-level calls: no point data passes through."""

    def __init__(self, pool: NodePool, names: CollectionNames | None = None, dimensions: int | None = None):
        self.pool = pool
        self.names = names or CollectionNames(settings.QDRANT_COLLECTION_PREFIX)
        self.dimensions = dimensions or settings.EMBEDDING_DIMENSIONS

    # ---- start-up ---------------------------------------------------------------------------------

    async def ensure(self, *, replacing: bool = False) -> None:
        """Make the collections exist and match the settings, or raise ``QdrantStartupError``.

        A collection that already exists is never altered: the shard count and the replication are fixed
        at creation, so a difference from the settings is reported instead of silently kept. The memory collection
        must also hold vectors of the embedder the settings describe (it records which one made them).
        ``replacing=True`` is for the re-embedding, which fills a new collection and moves the live alias to it: it
        accepts a live memory collection whose vectors are of another width or another embedder and whose layout
        (shards, replicas, write consistency) differs from the settings, and a links collection whose layout differs
        (the re-embedding rebuilds it). Everything else is still checked.
        """
        try:
            await self.pool.require_supported_server()
            # Creating a collection goes through the cluster's consensus, which can time out under load (a 500
            # answer); every step checks what exists first, so a repeat continues where the failed one stopped
            attempts = settings.QDRANT_WRITE_ATTEMPTS
            await self.pool.run(lambda client: self._ensure_links(client, replacing), attempts=attempts)
            await self.pool.run(lambda client: self._ensure_live(client, replacing), attempts=attempts)
        except (UnexpectedResponse, ResponseHandlingException) as exc:
            # A node that is down, or a key it refuses, is the most common reason a start fails: say so in a line
            raise QdrantStartupError(describe_request_failure(exc)) from exc

    async def _ensure_links(self, client: AsyncQdrantClient, replacing: bool) -> None:
        name = self.names.links
        if not await client.collection_exists(name):
            await self._create_links(client, name)
        await self._verify(client, name, vector_size=len(LINK_VECTOR), indexes=LINK_INDEXES, layout=not replacing)

    async def _create_links(self, client: AsyncQdrantClient, name: str) -> None:
        await self._create(
            client,
            name,
            qm.VectorParams(size=len(LINK_VECTOR), distance=qm.Distance.DOT, hnsw_config=qm.HnswConfigDiff(m=0)),
            hnsw=qm.HnswConfigDiff(m=0),
        )

    async def _ensure_live(self, client: AsyncQdrantClient, replacing: bool) -> None:
        alias = self.names.live
        generation = await self._generation_behind(client, alias)
        if generation is None:
            if await client.collection_exists(alias):
                raise QdrantStartupError(
                    f"Qdrant holds a collection named {alias}, which is the name of the live alias; "
                    "choose another QDRANT_COLLECTION_PREFIX or remove that collection",
                )
            generation = await self._create_memory_generation(client)
            await client.update_collection_aliases(
                change_aliases_operations=[
                    qm.CreateAliasOperation(create_alias=qm.CreateAlias(collection_name=generation, alias_name=alias)),
                ],
            )
            logger.info("Created the memory collection", extra={"generation": generation})
        await self._verify(
            client,
            generation,
            vector_size=None if replacing else self.dimensions,
            indexes=MEMORY_INDEXES,
            layout=not replacing,
        )
        if not replacing:
            await self._check_embedder(client, generation)

    async def _check_embedder(self, client: AsyncQdrantClient, generation: str) -> None:
        """Refuse a memory collection whose vectors another embedder made; record the embedder of one that says nothing.

        A switch of provider, model or precision that keeps the width passes the width check, and queries would then be
        embedded in another vector space than the stored vectors. A collection made before the embedder was recorded is
        taken to be the configured one and records it now.
        """
        wanted = embedder_identity(self.dimensions)
        recorded = ((await client.get_collection(generation)).config.metadata or {}).get(EMBEDDER_KEY)
        if recorded is None:
            await client.update_collection(generation, metadata={EMBEDDER_KEY: wanted})
            logger.warning(
                "The memory collection did not record which embedder made its vectors; recorded the configured one",
                extra={"generation": generation},
            )
        elif recorded != wanted:
            raise QdrantStartupError(
                f"Qdrant collection {generation} holds vectors made with {_describe_identity(recorded)}, but the "
                f"settings describe {_describe_identity(wanted)} (EMBEDDING_PROVIDER, EMBEDDING_MODEL, EMBEDDING_DIMENSIONS, "
                "EMBEDDING_TF32, GATEWAY_BACKEND, GATEWAY_MODEL). Run `axmem re-embed` to embed the memories again with "
                "the configured embedder, or change the settings back",
            )

    # ---- generations ------------------------------------------------------------------------------

    async def _read[T](self, call: Callable[[AsyncQdrantClient], Awaitable[T]]) -> T:
        """Run a read on the first node that answers: a cluster with a silent node is still inspectable."""
        return await self.pool.run(call, attempts=len(self.pool.clients))

    async def live_generation(self) -> str:
        """Name of the collection the live alias points at."""
        generation = await self._read(lambda client: self._generation_behind(client, self.names.live))
        if generation is None:
            raise QdrantStartupError(f"The alias {self.names.live} does not exist yet; start the server once to create it")
        return generation

    async def live_exists(self) -> bool:
        return await self._read(lambda client: self._generation_behind(client, self.names.live)) is not None

    async def create_generation(self) -> str:
        """Create an empty memory collection with the configured dimensions and settings; return its name."""
        return await self.pool.run(self._create_memory_generation, attempts=settings.QDRANT_WRITE_ATTEMPTS)

    async def activate(self, generation: str) -> None:
        """Point the live alias at ``generation``: one request, so readers see the old or the new generation, never neither.

        Repeating it after an ambiguous failure is safe: the alias is looked at first, so an answer that was lost does
        not make the repeat fail on an alias that already moved. The alias is created when it does not exist yet.
        """
        alias = self.names.live

        async def switch(client: AsyncQdrantClient) -> None:
            behind = await self._generation_behind(client, alias)
            if behind == generation:
                return
            operations: list[qm.AliasOperations] = []
            if behind is not None:
                operations.append(qm.DeleteAliasOperation(delete_alias=qm.DeleteAlias(alias_name=alias)))
            operations.append(
                qm.CreateAliasOperation(create_alias=qm.CreateAlias(collection_name=generation, alias_name=alias)),
            )
            await client.update_collection_aliases(change_aliases_operations=operations)

        await self.pool.run(switch, attempts=settings.QDRANT_WRITE_ATTEMPTS)
        logger.info("Activated memory generation", extra={"generation": generation})

    async def make_live(self, generation: str) -> None:
        """Make ``generation`` the live one whether or not the alias exists yet."""
        await self.activate(generation)

    async def discard(self, collection: str) -> None:
        """Delete a collection; one that does not exist is not an error (Qdrant answers that with ``false``)."""
        await self.pool.run(lambda client: client.delete_collection(collection), attempts=settings.QDRANT_WRITE_ATTEMPTS)

    async def exists(self, collection: str) -> bool:
        return await self._read(lambda client: client.collection_exists(collection))

    async def links_layout_differs(self) -> bool:
        """Whether the links collection exists with a shard count, replication factor or write consistency factor other than the settings'."""
        if not await self.exists(self.names.links):
            return False
        info = await self._read(lambda client: client.get_collection(self.names.links))
        return bool(_layout_problems(info.config.params))

    async def create_links_collection(self, name: str) -> None:
        """Create a collection of the links' shape (vector, indexes) in the layout the settings describe, or check the one that exists."""
        async def create(client: AsyncQdrantClient) -> None:
            if not await client.collection_exists(name):
                await self._create_links(client, name)
            await self._verify(client, name, vector_size=len(LINK_VECTOR), indexes=LINK_INDEXES)

        await self.pool.run(create, attempts=settings.QDRANT_WRITE_ATTEMPTS)

    async def vector_width(self, generation: str) -> int | None:
        """How many numbers each vector of a generation has."""
        info = await self._read(lambda client: client.get_collection(generation))
        vectors = info.config.params.vectors
        return vectors.size if isinstance(vectors, qm.VectorParams) else None

    # ---- helpers ----------------------------------------------------------------------------------

    @staticmethod
    async def _generation_behind(client: AsyncQdrantClient, alias: str) -> str | None:
        aliases = await client.get_aliases()
        for description in aliases.aliases:
            if description.alias_name == alias:
                return description.collection_name
        return None

    async def _create_memory_generation(self, client: AsyncQdrantClient) -> str:
        name = self.names.new_generation()
        await self._create(
            client,
            name,
            qm.VectorParams(size=self.dimensions, distance=qm.Distance.COSINE),
            hnsw=None,
            metadata={EMBEDDER_KEY: embedder_identity(self.dimensions)},
        )
        try:
            await self._ensure_indexes(client, name, MEMORY_INDEXES)
        except BaseException:
            # A generation without its indexes is never used, and every failed start would leave another behind
            with contextlib.suppress(Exception):
                await client.delete_collection(name)
            raise
        return name

    @staticmethod
    async def _create(
        client: AsyncQdrantClient,
        name: str,
        vectors: qm.VectorParams,
        hnsw: qm.HnswConfigDiff | None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        try:
            await client.create_collection(
                name,
                vectors_config=vectors,
                shard_number=settings.QDRANT_SHARD_NUMBER,
                replication_factor=settings.QDRANT_REPLICATION_FACTOR,
                write_consistency_factor=settings.QDRANT_WRITE_CONSISTENCY_FACTOR,
                hnsw_config=hnsw,
                metadata=metadata,
            )
        except UnexpectedResponse as exc:
            # Another process created it between our check and this request: use theirs, verified below
            if exc.status_code != 409:
                raise

    @staticmethod
    async def _ensure_indexes(
        client: AsyncQdrantClient,
        name: str,
        indexes: dict[str, qm.KeywordIndexParams | qm.PayloadSchemaType],
    ) -> None:
        info = await client.get_collection(name)
        for field, spec in indexes.items():
            if field not in info.payload_schema:
                await client.create_payload_index(name, field, field_schema=spec, wait=True)

    async def _verify(
        self,
        client: AsyncQdrantClient,
        name: str,
        vector_size: int | None,
        indexes: dict[str, qm.KeywordIndexParams | qm.PayloadSchemaType],
        layout: bool = True,
    ) -> None:
        """Raise ``QdrantStartupError`` when the collection differs from the settings in a way that cannot be changed.

        ``layout=False`` leaves the shard count, the replication factor and the write consistency factor unchecked,
        for a collection that a re-embedding is about to replace.
        """
        info = await client.get_collection(name)
        params = info.config.params
        vectors = params.vectors
        found_size = vectors.size if isinstance(vectors, qm.VectorParams) else None
        problems: list[str] = []
        if vector_size is not None and found_size != vector_size:
            problems.append(
                f"vectors are {found_size} wide but {vector_size} are configured "
                "(EMBEDDING_DIMENSIONS; run `axmem re-embed` after changing the model)",
            )
        if layout:
            problems.extend(_layout_problems(params))
        if problems:
            raise QdrantStartupError(
                f"Qdrant collection {name} does not match the settings: " + "; ".join(problems)
                + ". These are fixed when a collection is created: change the settings back, or run `axmem re-embed`, "
                "which moves the memories to a collection of the layout the settings describe and rebuilds the links "
                "collection in it.",
            )
        for field, spec in indexes.items():
            existing = info.payload_schema.get(field)
            if existing is not None and _index_type(existing) != _expected_index_type(spec):
                raise QdrantStartupError(
                    f"Qdrant collection {name}: the payload index of {field} is {_index_type(existing)} "
                    f"but {_expected_index_type(spec)} is required",
                )
        await self._ensure_indexes(client, name, indexes)
