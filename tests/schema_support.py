"""What the tests of the Qdrant collection layout share: nodes that note, fail or do not answer, and a changed embedder.

The recorder notes the requests that look at or change the collections; the node is the real one and a mismatch is made
by creating the collection the way an earlier configuration would have.
"""
from typing import Any

from qdrant_client.http.exceptions import UnexpectedResponse

from app.repositories.qdrant.schema import EMBEDDER_KEY, embedder_identity

Calls = list[tuple[str, tuple, dict]]

CHANGING = {
    "create_collection", "create_payload_index", "update_collection_aliases", "update_collection", "delete_collection",
    "get_collection", "get_aliases", "collection_exists",
}
OTHER_MODEL = "another/model-of-the-same-width"


class Recorder:
    """The real client, noting the name and the arguments of the requests that look at or change the collections."""

    def __init__(self, client: Any, calls: Calls):
        self._client = client
        self.calls = calls

    def __getattr__(self, name: str) -> Any:
        target = getattr(self._client, name)
        if name not in CHANGING:
            return target

        async def recorded(*args: Any, **kwargs: Any) -> Any:
            self.calls.append((name, args, kwargs))
            return await target(*args, **kwargs)

        return recorded


class FailsOnce(Recorder):
    """A node whose first request of one kind times out with a 500 (a consensus timeout) before it reaches the node."""

    def __init__(self, client: Any, calls: Calls, method: str):
        super().__init__(client, calls)
        self._method = method
        self._failed = False

    def __getattr__(self, name: str) -> Any:
        if name != self._method:
            return super().__getattr__(name)

        async def failing_once(*args: Any, **kwargs: Any) -> Any:
            self.calls.append((name, args, kwargs))
            if not self._failed:
                self._failed = True
                raise UnexpectedResponse(500, "reason", b"consensus timeout", None)
            return await getattr(self._client, name)(*args, **kwargs)

        return failing_once


class Unreachable:
    """A node that does not answer."""

    async def close(self) -> None:
        """Nothing to close."""

    def __getattr__(self, name: str) -> Any:
        async def refuse(*args: Any, **kwargs: Any) -> Any:
            raise TimeoutError

        return refuse


class Stop(BaseException):
    """What ends a task from outside (it is not an ``Exception``)."""


def record_requests(pool: Any) -> Calls:
    """Put a recorder before each client of the pool; the list it fills is returned."""
    recorded: Calls = []
    pool.clients = [Recorder(client, recorded) for client in pool.clients]
    return recorded


def changes(calls: Calls, *names: str) -> Calls:
    return [call for call in calls if call[0] in names]


async def other_embedder_live(pool: Any, schema: Any) -> str:
    """Make a generation that records ``OTHER_MODEL`` the live one."""
    generation = await schema.create_generation()
    identity = embedder_identity(schema.dimensions)
    await pool.clients[0].update_collection(generation, metadata={EMBEDDER_KEY: {**identity, "model": OTHER_MODEL}})
    await schema.activate(generation)
    return generation
