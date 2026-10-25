"""How a restore reads what a node says about the replicas of a collection, one answer at a time.

``test_qdrant_backup_contract.py`` restores whole backup sets and pins when a restore gives up on a replica. This file
asks the wait itself, with the answers of the node given by hand: which states count as Active, how an answer that
lacks a list is read, what the text of the states looks like, and that a wait whose replicas never settle is
ended by the test and not by a deadline of minutes. The node is played by a stub of the HTTP answer (nothing else of the
adapter is replaced) and the checks are kept light: a mutated wait that cannot see an Active replica would otherwise
keep every restore test waiting for its 90 s or 600 s.
"""
import pytest

from app.exceptions import OperatorCommandError
from app.repositories.qdrant import backup as backup_module
from app.repositories.qdrant.backup import QdrantBackupAdapter

COLLECTION = "axmem_20260101t000000abcd"
ACTIVE = {"state": "Active"}
POLLS_ALLOWED = 6  # a wait that polls more often than this does not see what the test shows it


class Answer:
    """The part of an HTTP response that the wait reads."""

    def __init__(self, status_code: int, body: dict) -> None:
        self.status_code = status_code
        self._body = body

    def json(self) -> dict:
        return self._body


class Node:
    """A node that answers each poll with the next answer given (the last one again), and fails the test on a poll too many."""

    def __init__(self, *answers: Answer) -> None:
        self.urls = ["http://node-0", "http://node-1"]
        self.answers = list(answers)
        self.opened: list[str] = []
        self.asked: list[str] = []

    def http(self, url: str, timeout: float | None = None) -> Node:
        self.opened.append(url)
        return self

    async def __aenter__(self) -> Node:
        return self

    async def __aexit__(self, *error) -> None:
        return None

    async def get(self, path: str) -> Answer:
        self.asked.append(path)
        assert len(self.asked) <= POLLS_ALLOWED, "the wait went on polling after the replicas had settled"
        return self.answers.pop(0) if len(self.answers) > 1 else self.answers[0]


def reporting(result: dict | None, status: int = 200) -> Answer:
    return Answer(status, {} if result is None else {"result": result})


@pytest.fixture(autouse=True)
def quick_polls(monkeypatch):
    monkeypatch.setattr(backup_module, "REPLICA_POLL_SECONDS", 0)


async def waited(node: Node, within: float) -> str | None:
    return await QdrantBackupAdapter(node, schema=object())._wait_until_active(COLLECTION, 1, within)


@pytest.mark.parametrize(
    ("result", "expected"),
    [
        ({"local_shards": [ACTIVE], "remote_shards": [ACTIVE]}, None),
        ({"local_shards": [ACTIVE, ACTIVE]}, None),
        ({"remote_shards": [ACTIVE]}, None),
        ({"local_shards": [ACTIVE], "remote_shards": [{"state": "Recovery"}]}, "Active, Recovery"),
        ({"local_shards": [{"state": "Recovery"}], "remote_shards": [ACTIVE]}, "Active, Recovery"),
        ({"local_shards": [{"state": "Recovery"}]}, "Recovery"),
        ({"remote_shards": [{"state": "Dead"}]}, "Dead"),
        (
            {"local_shards": [{"state": "ManualRecovery"}, {"state": "Dead"}], "remote_shards": [ACTIVE, {"state": "Dead"}]},
            "Active, Dead, Dead, ManualRecovery",
        ),
        ({"local_shards": [{"state": "active"}]}, "active"),
        ({"local_shards": [{"state": "ACTIVE"}]}, "ACTIVE"),
        ({"local_shards": [{}]}, "None"),
        ({"local_shards": [], "remote_shards": []}, "none reported"),
        ({}, "none reported"),
        (None, "none reported"),
    ],
    ids=[
        "both-active", "local-only", "remote-only", "remote-recovering", "local-recovering", "local-recovering-alone",
        "remote-dead-alone", "states-sorted", "lower-case", "upper-case", "no-state", "no-shards", "empty-result", "no-result",
    ],
)
async def test_the_states_of_the_local_and_the_remote_replicas_are_all_read_and_only_all_active_settles_them(result, expected):
    node = Node(reporting(result))

    assert await waited(node, within=0.0) == expected

    assert node.opened == ["http://node-1"]
    assert node.asked == [f"/collections/{COLLECTION}/cluster"]


async def test_a_wait_whose_time_is_up_at_its_first_poll_ends_with_the_states_of_that_poll():
    node = Node(reporting({"local_shards": [{"state": "Recovery"}]}), reporting({"local_shards": [{"state": "Dead"}]}))

    assert await waited(node, within=0.0) == "Recovery"  # the first answer, not the last
    assert len(node.asked) == 1


async def test_a_replica_that_turns_active_while_the_wait_lasts_ends_the_wait():
    recovering = reporting({"local_shards": [{"state": "Recovery"}]})
    node = Node(recovering, recovering, reporting({"local_shards": [ACTIVE]}))

    assert await waited(node, within=60.0) is None

    assert len(node.asked) == 3


@pytest.mark.parametrize("status", [400, 404, 500])
async def test_an_answer_from_400_up_ends_the_wait_with_the_status_and_the_node(status):
    node = Node(reporting({"local_shards": [ACTIVE]}, status=status))

    with pytest.raises(OperatorCommandError) as refused:
        await waited(node, within=60.0)

    assert str(refused.value) == f"Qdrant at http://node-1 would not report the replicas of {COLLECTION}: HTTP {status}"
    assert len(node.asked) == 1

