"""The only way the repositories read and write Qdrant points: the tenant filter chokepoint.

An API key admits every tenant, so tenant isolation is this module's job. Every method takes a ``Scope``
first. A tenant scope adds ``tenant_id == <tenant>`` to the filter of every read, to the guard of every
conditional write, and to the payload of every new point, in the one function that builds a filter
(``scoped``). The repositories never see a client, so no query can be written that skips it. The
``SYSTEM`` scope is for the operations that span tenants (re-embedding, export, snapshots, the
consistency check); the architecture tests list which repository methods may use it.

Every write goes through ``_write``, which fixes the options the verified cluster profile needs:
``wait=True`` (the response says nothing about whether a conditional write won, so the caller has to
read the point back), strong write ordering, and an explicit ``update_mode`` for every upsert.
"""
import asyncio
import logging
from collections.abc import AsyncIterator, Awaitable, Callable, Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any, TypeVar
from uuid import UUID

from qdrant_client import AsyncQdrantClient
from qdrant_client import models as qm

from app.config.settings import settings
from app.exceptions import ConflictError, OutcomeUnknownError
from app.repositories.qdrant.client import NodePool

logger = logging.getLogger(__name__)

T = TypeVar("T")

TENANT_KEY = "tenant_id"
VERSION_KEY = "version"
TOKEN_KEY = "op_token"  # noqa: S105  # nosec B105 - the name of the payload field that holds the claim token of a write, not a credential
HISTORY_KEY = "op_history"
# How many earlier claim tokens a point keeps next to its own: the writes that can land between a conditional write
# and its read-back without making the outcome of that write unknowable
CLAIM_HISTORY = 16
# How often a majority read is repeated for a point that the replicas hold in different versions at that moment, and the
# wait before the first repeat (doubled each time): writes that are in flight land within a few hundred milliseconds
AGREEMENT_ATTEMPTS = 4
AGREEMENT_WAIT_SECONDS = 0.05


@dataclass(frozen=True)
class Scope:
    """Whose points an operation may touch: one tenant's, or (``SYSTEM``) every tenant's."""

    tenant_id: str | None

    @classmethod
    def of(cls, tenant: UUID) -> Scope:
        return cls(str(tenant))


SYSTEM = Scope(None)


@dataclass(frozen=True)
class Point:
    """A point read back from Qdrant."""

    id: UUID
    payload: dict[str, Any]
    vector: list[float] | None = None
    score: float = 0.0


@dataclass(frozen=True)
class NewPoint:
    """A point to write."""

    id: UUID
    vector: list[float]
    payload: dict[str, Any]


@dataclass(frozen=True)
class Where:
    """Conditions on the payload, combined with the tenant condition by ``scoped``."""

    must: tuple[qm.Condition, ...] = ()
    must_not: tuple[qm.Condition, ...] = ()
    should: tuple[qm.Condition, ...] = ()

    def also(self, *conditions: qm.Condition) -> Where:
        return Where(self.must + conditions, self.must_not, self.should)

    def excluding(self, *conditions: qm.Condition) -> Where:
        return Where(self.must, self.must_not + conditions, self.should)

    def either(self, *conditions: qm.Condition) -> Where:
        return Where(self.must, self.must_not, self.should + conditions)


def equals(key: str, value: str | int | bool) -> qm.Condition:
    return qm.FieldCondition(key=key, match=qm.MatchValue(value=value))


def any_of(key: str, values: Iterable[str]) -> qm.Condition:
    return qm.FieldCondition(key=key, match=qm.MatchAny(any=list(values)))


def has_ids(ids: Iterable[UUID]) -> qm.Condition:
    return qm.HasIdCondition(has_id=[str(point_id) for point_id in ids])


def at_least(key: str, value: int) -> qm.Condition:
    return qm.FieldCondition(key=key, range=qm.Range(gte=value))


def below(key: str, value: int) -> qm.Condition:
    return qm.FieldCondition(key=key, range=qm.Range(lt=value))


def since(key: str, moment: datetime) -> qm.Condition:
    return qm.FieldCondition(key=key, range=qm.DatetimeRange(gte=moment))


def scoped(scope: Scope, where: Where | None = None) -> qm.Filter:
    """The filter of an operation: the tenant condition of ``scope`` first, then the caller's conditions.

    Every filter this module sends is built here.
    """
    where = where or Where()
    must = list(where.must)
    if scope.tenant_id is not None:
        must.insert(0, equals(TENANT_KEY, scope.tenant_id))
    return qm.Filter(must=must or None, must_not=list(where.must_not) or None, should=list(where.should) or None)


def claim_fields(current: dict[str, Any] | None, token: str) -> dict[str, Any]:
    """The claim fields of a write that follows the state ``current`` (None for a first write): its token, and the
    tokens of the writes before it, newest first, as many as ``CLAIM_HISTORY`` allows.

    ``current`` must be the payload at the version the write is conditional on: no other write can have moved the
    point on from that version, so these are exactly the tokens the point holds when the write lands.
    """
    earlier = [] if current is None else [current.get(TOKEN_KEY), *(current.get(HISTORY_KEY) or ())]
    return {TOKEN_KEY: token, HISTORY_KEY: earlier[:CLAIM_HISTORY]}


def write_landed(payload: dict[str, Any], *, from_version: int, token: str) -> bool | None:
    """Whether the write that moved a point on from ``from_version`` carried ``token``, according to what the point says now.

    Each write is conditional on the version it read, so only one write can ever move a point from one version to the
    next, and the token stored for version ``from_version + 1`` names it, however many writes came after. A point
    holds its own token and ``CLAIM_HISTORY`` earlier ones, newest first: the n-th of them belongs to version
    ``version - n``. Returns None when that token has been pushed out of the history (more than ``CLAIM_HISTORY``
    writes landed since), which means the outcome cannot be told.
    """
    distance = int(payload[VERSION_KEY]) - (from_version + 1)
    if distance < 0:
        return False  # the point has not got that far: the write did not apply
    history = payload.get(HISTORY_KEY) or []
    tokens = [payload.get(TOKEN_KEY), *history]
    if distance < len(tokens):
        return tokens[distance] == token
    # A history that is not full holds every write since the point's record began, so a version before that is a write
    # the record never saw (a copy of a newer point replaced it); a full one has dropped the token
    return None if len(history) >= CLAIM_HISTORY else False


def _point(record: Any) -> Point:
    return Point(
        id=UUID(str(record.id)),
        payload=dict(record.payload or {}),
        vector=list(record.vector) if isinstance(record.vector, list) else None,
        score=float(getattr(record, "score", 0.0)),
    )


class QdrantStore:
    """Scoped reads and conditional writes on the collections; the one holder of the Qdrant connections."""

    def __init__(self, pool: NodePool):
        self.pool = pool

    # ---- reads -------------------------------------------------------------------------------------

    async def _read(self, call: Callable[[AsyncQdrantClient], Awaitable[T]]) -> T:
        return await self.pool.run(call, attempts=len(self.pool.clients))

    async def scroll(
        self,
        scope: Scope,
        collection: str,
        where: Where | None = None,
        *,
        limit: int,
        start_from: UUID | None = None,
        order_by: tuple[str, str] | None = None,
        with_payload: bool | list[str] = True,
        with_vectors: bool = False,
        quorum: bool = False,
    ) -> list[Point]:
        """Up to ``limit`` points matching the filter, by ascending id (or ``order_by`` = ``(field, "asc"|"desc")``).

        ``start_from`` continues an id-ordered scan at that id, inclusive. ``quorum`` asks a majority of
        the replicas, which is what reading back the outcome of a write needs (Qdrant's ``majority`` level asks every
        replica and fails while one is down; ``quorum`` asks just enough of them, so a cluster with a node gone still answers).
        """
        ordering = None
        if order_by is not None:
            field_name, direction = order_by
            ordering = qm.OrderBy(
                key=field_name, direction=qm.Direction.DESC if direction == "desc" else qm.Direction.ASC,
            )
        flt = scoped(scope, where)

        async def call(client: AsyncQdrantClient) -> list[Any]:
            records, _ = await client.scroll(
                collection,
                scroll_filter=flt,
                limit=limit,
                order_by=ordering,
                offset=str(start_from) if start_from is not None else None,
                with_payload=with_payload,
                with_vectors=with_vectors,
                consistency=qm.ReadConsistencyType.QUORUM if quorum else None,
            )
            return list(records)

        return [_point(record) for record in await self._read(call)]

    async def get(
        self,
        scope: Scope,
        collection: str,
        ids: Sequence[UUID],
        *,
        with_payload: bool | list[str] = True,
        with_vectors: bool = False,
        quorum: bool = False,
    ) -> list[Point]:
        """The points among ``ids`` that exist and that ``scope`` may see, in no particular order."""
        wanted = list(dict.fromkeys(ids))
        if not wanted:
            return []
        return await self.scroll(
            scope,
            collection,
            Where().also(has_ids(wanted)),
            limit=len(wanted),
            with_payload=with_payload,
            with_vectors=with_vectors,
            quorum=quorum,
        )

    async def get_agreed(
        self,
        scope: Scope,
        collection: str,
        ids: Sequence[UUID],
        *,
        with_payload: bool | list[str] = True,
        with_vectors: bool = False,
    ) -> list[Point]:
        """The points among ``ids`` that exist, as a majority of the replicas hold them.

        A majority read returns a point only when the replicas it asks hold the same version of it, so a point that is
        being rewritten at that moment is missing from the answer although it exists. A point that the majority read
        does not return and that a plain read finds is that case: the read is repeated, waiting a little longer each
        time, and gives up with ``ConflictError`` when the replicas never agree. A point no read finds does not exist.

        Raises:
            ConflictError: If the replicas kept holding different versions of a point
        """
        wanted = list(dict.fromkeys(ids))
        missing = wanted
        wait = AGREEMENT_WAIT_SECONDS
        for attempt in range(AGREEMENT_ATTEMPTS):
            found = await self.get(
                scope, collection, wanted, with_payload=with_payload, with_vectors=with_vectors, quorum=True,
            )
            agreed = {point.id for point in found}
            missing = [point_id for point_id in wanted if point_id not in agreed]
            if not missing or not await self.get(scope, collection, missing, with_payload=False):
                return found
            if attempt + 1 < AGREEMENT_ATTEMPTS:
                await asyncio.sleep(wait)
                wait *= 2
        raise ConflictError(
            f"The replicas of {collection} kept holding different versions of {', '.join(map(str, missing))}; "
            "nothing was changed, try again",
        )

    async def iterate(
        self,
        scope: Scope,
        collection: str,
        where: Where | None = None,
        *,
        batch: int = 256,
        with_payload: bool | list[str] = True,
        with_vectors: bool = False,
        quorum: bool = False,
    ) -> AsyncIterator[Point]:
        """Every point matching the filter, by ascending id, ``batch`` at a time (``quorum``: each page from a majority)."""
        start: UUID | None = None
        while True:
            points = await self.scroll(
                scope, collection, where, limit=batch + 1, start_from=start,
                with_payload=with_payload, with_vectors=with_vectors, quorum=quorum,
            )
            for point in points[:batch]:
                yield point
            if len(points) <= batch:
                return
            start = points[batch].id

    async def count(self, scope: Scope, collection: str, where: Where | None = None) -> int:
        flt = scoped(scope, where)

        async def call(client: AsyncQdrantClient) -> int:
            return (await client.count(collection, count_filter=flt, exact=True)).count

        return await self._read(call)

    async def search(
        self,
        scope: Scope,
        collection: str,
        *,
        query: list[float],
        where: Where | None = None,
        limit: int,
        score_threshold: float | None = None,
        with_vectors: bool = False,
    ) -> list[Point]:
        """The ``limit`` nearest points by cosine similarity, best first.

        The query is always a vector. A stored point is never named as the query by its id, because Qdrant
        looks that point up without applying the filter, which would let one tenant search with another's vector.
        """
        flt = scoped(scope, where)

        async def call(client: AsyncQdrantClient) -> list[Any]:
            response = await client.query_points(
                collection,
                query=query,
                query_filter=flt,
                limit=limit,
                score_threshold=score_threshold,
                with_payload=True,
                with_vectors=with_vectors,
            )
            return list(response.points)

        return [_point(record) for record in await self._read(call)]

    # ---- writes ------------------------------------------------------------------------------------

    async def _write(self, call: Callable[[AsyncQdrantClient], Awaitable[Any]]) -> None:
        """Run a write on a node, repeating the very same request after an ambiguous failure."""
        await self.pool.run(call, attempts=settings.QDRANT_WRITE_ATTEMPTS)

    @staticmethod
    def _tenant_payload(scope: Scope, payload: dict[str, Any]) -> dict[str, Any]:
        """The payload with the tenant of ``scope`` stamped on it (a system write must already carry one)."""
        if scope.tenant_id is None:
            if not payload.get(TENANT_KEY):
                raise ValueError("a point written in the system scope must carry its tenant_id")
            return payload
        return {**payload, TENANT_KEY: scope.tenant_id}

    async def insert(self, scope: Scope, collection: str, points: Sequence[NewPoint]) -> None:
        """Create the points that do not exist yet; one that exists is left exactly as it is.

        ``insert_only`` makes this an atomic create-if-absent, so a repeated request cannot overwrite.
        """
        if not points:
            return  # Qdrant rejects an update with no points
        structs = [
            qm.PointStruct(id=str(point.id), vector=point.vector, payload=self._tenant_payload(scope, point.payload))
            for point in points
        ]

        async def call(client: AsyncQdrantClient) -> None:
            await client.upsert(
                collection,
                structs,
                wait=True,
                ordering=qm.WriteOrdering.STRONG,
                update_mode=qm.UpdateMode.INSERT_ONLY,
            )

        await self._write(call)

    async def replace(
        self,
        scope: Scope,
        collection: str,
        point: NewPoint,
        *,
        expected_version: int | None = None,
        older_than: int | None = None,
    ) -> None:
        """Replace the whole point, but only while its version is ``expected_version`` (or below ``older_than``).

        ``update_only`` keeps a missing point missing; the condition is part of the write, so a concurrent
        edit that committed first makes this one a silent no-op. The response says nothing about which
        happened: read the point back and compare the claim token.
        """
        if (expected_version is None) == (older_than is None):
            raise ValueError("replace needs exactly one of expected_version and older_than")
        guard = (
            equals(VERSION_KEY, expected_version)
            if expected_version is not None
            else below(VERSION_KEY, older_than or 0)
        )
        update_filter = scoped(scope, Where().also(guard))
        struct = qm.PointStruct(
            id=str(point.id), vector=point.vector, payload=self._tenant_payload(scope, point.payload),
        )

        async def call(client: AsyncQdrantClient) -> None:
            await client.upsert(
                collection,
                [struct],
                wait=True,
                ordering=qm.WriteOrdering.STRONG,
                update_mode=qm.UpdateMode.UPDATE_ONLY,
                update_filter=update_filter,
            )

        await self._write(call)

    async def patch(
        self,
        scope: Scope,
        collection: str,
        point_id: UUID,
        payload: dict[str, Any],
        expected: Where,
    ) -> None:
        """Set payload fields of one point, but only while ``expected`` holds for it.

        The selector is a filter that names the point (``HasId``) and the guard; a missing point or a
        failed guard is a silent no-op, so read the point back and compare the claim token.
        """
        selector = scoped(scope, expected.also(has_ids([point_id])))

        async def call(client: AsyncQdrantClient) -> None:
            await client.set_payload(
                collection,
                payload=payload,
                points=selector,
                wait=True,
                ordering=qm.WriteOrdering.STRONG,
            )

        await self._write(call)

    async def delete(self, scope: Scope, collection: str, ids: Sequence[UUID]) -> None:
        """Delete the points among ``ids`` that ``scope`` may see; deleting what does not exist is not an error."""
        if not ids:
            return
        selector = qm.FilterSelector(filter=scoped(scope, Where().also(has_ids(ids))))

        async def call(client: AsyncQdrantClient) -> None:
            await client.delete(collection, points_selector=selector, wait=True, ordering=qm.WriteOrdering.STRONG)

        await self._write(call)

    async def landed(
        self, scope: Scope, collection: str, point_id: UUID, *, from_version: int, token: str,
    ) -> bool:
        """Whether the conditional write guarded on ``from_version`` that carried ``token`` is the one that applied.

        The response of a conditional write does not say, so this reads the point back from a majority of the replicas
        and looks for the token in the claim history, which later writes extend and do not overwrite. A majority read
        omits a point while the replicas hold different versions of it, which is what a point that other writers are
        rewriting looks like, so the read-back waits for the replicas to agree like any read that answers for one point
        (``get_agreed``); an empty answer is not taken for "the write was lost".

        Returns:
            False when the point does not exist for ``scope`` in ``collection`` (a write to it cannot have applied)

        Raises:
            OutcomeUnknownError: If more than ``CLAIM_HISTORY`` writes landed since, so the token is gone, or the
                replicas kept holding different versions of the point, so it cannot be read back
        """
        try:
            points = await self.get_agreed(
                scope, collection, [point_id], with_payload=[VERSION_KEY, TOKEN_KEY, HISTORY_KEY],
            )
        except ConflictError as exc:
            raise OutcomeUnknownError(
                f"The outcome of a write to {point_id} cannot be told: the replicas of {collection} kept holding "
                "different versions of it when it was read back. It may or may not have been applied; read it again "
                "and repeat the change if it is missing",
            ) from exc
        if not points:
            logger.warning(
                "A point could not be read back after a write because it does not exist; the write was not applied",
                extra={"point_id": str(point_id), "collection": collection},
            )
            return False
        outcome = write_landed(points[0].payload, from_version=from_version, token=token)
        if outcome is None:
            raise OutcomeUnknownError(
                f"The outcome of a write to {point_id} cannot be told: more than {CLAIM_HISTORY} other writes landed "
                "before it was read back. It may or may not have been applied; read it again and repeat the change "
                "if it is missing",
            )
        return outcome
