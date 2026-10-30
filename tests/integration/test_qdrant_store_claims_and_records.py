# SPDX-FileCopyrightText: 2026 Giovanni Manzoni
# SPDX-License-Identifier: AGPL-3.0-only

"""The claim tokens that tell whether a write landed, and how a record that Qdrant returns is read, exactly.

Neither needs a node: the claim fields are plain dicts, and a record is any object with an id, a payload and a vector.
"""
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.repositories.qdrant.store import (
    CLAIM_HISTORY,
    HISTORY_KEY,
    TOKEN_KEY,
    VERSION_KEY,
    Point,
    _point,
    claim_fields,
    write_landed,
)


def test_the_claim_fields_of_a_first_write_hold_its_token_and_no_history():
    assert claim_fields(None, "first") == {TOKEN_KEY: "first", HISTORY_KEY: []}


def test_the_claim_fields_of_a_later_write_put_the_token_of_the_point_in_front_of_its_history():
    current = {TOKEN_KEY: "t2", HISTORY_KEY: ["t1", "t0"]}

    assert claim_fields(current, "t3") == {TOKEN_KEY: "t3", HISTORY_KEY: ["t2", "t1", "t0"]}
    assert claim_fields({TOKEN_KEY: "t1"}, "t2") == {TOKEN_KEY: "t2", HISTORY_KEY: ["t1"]}
    assert claim_fields({}, "t1") == {TOKEN_KEY: "t1", HISTORY_KEY: [None]}
    assert current == {TOKEN_KEY: "t2", HISTORY_KEY: ["t1", "t0"]}


def test_the_claim_history_keeps_exactly_the_newest_tokens_the_limit_allows():
    full = {TOKEN_KEY: "now", HISTORY_KEY: [f"h{index}" for index in range(CLAIM_HISTORY)]}
    almost = {TOKEN_KEY: "now", HISTORY_KEY: [f"h{index}" for index in range(CLAIM_HISTORY - 1)]}

    assert claim_fields(full, "next")[HISTORY_KEY] == ["now", *(f"h{index}" for index in range(CLAIM_HISTORY - 1))]
    assert claim_fields(almost, "next")[HISTORY_KEY] == ["now", *(f"h{index}" for index in range(CLAIM_HISTORY - 1))]
    assert len(claim_fields(full, "next")[HISTORY_KEY]) == CLAIM_HISTORY == 16


@pytest.mark.parametrize(("version", "from_version", "token", "expected"), [
    (5, 4, "t5", True),        # the next version: the token of the point itself
    (5, 4, "t4", False),
    (5, 3, "t4", True),        # one write after: the newest token of the history
    (5, 3, "t5", False),
    (5, 1, "t2", True),        # the oldest transition the record still holds
    (5, 1, "t3", False),
    (5, 5, "t5", False),       # the point has not moved on from that version
    (5, 6, "t5", False),
    (5, 9, "t10", False),
])
def test_the_token_of_the_transition_from_a_version_is_found_in_the_claim_history(version, from_version, token, expected):
    payload = {VERSION_KEY: version, TOKEN_KEY: f"t{version}", HISTORY_KEY: [f"t{n}" for n in range(version - 1, 1, -1)]}

    assert write_landed(payload, from_version=from_version, token=token) is expected


def test_the_outcome_is_unknown_only_when_the_history_is_full_and_the_token_is_beyond_it():
    def point_at(version: int, held: int) -> dict:
        return {VERSION_KEY: version, TOKEN_KEY: "now", HISTORY_KEY: [f"h{index}" for index in range(held)]}

    # a history of 16 holds the versions 25..9 (token and 16 before it); version 8 is beyond it
    assert write_landed(point_at(25, CLAIM_HISTORY), from_version=24, token="now") is True
    assert write_landed(point_at(25, CLAIM_HISTORY), from_version=8, token="h15") is True
    assert write_landed(point_at(25, CLAIM_HISTORY), from_version=7, token="h15") is None
    # a history that is not full holds every write of the record: a version before it is a write the record never saw
    assert write_landed(point_at(25, CLAIM_HISTORY - 1), from_version=9, token="h14") is True
    assert write_landed(point_at(25, CLAIM_HISTORY - 1), from_version=8, token="h14") is False
    assert write_landed({VERSION_KEY: 3, TOKEN_KEY: "now", HISTORY_KEY: None}, from_version=2, token="now") is True
    assert write_landed({VERSION_KEY: 3, TOKEN_KEY: "now", HISTORY_KEY: None}, from_version=1, token="now") is False
    assert write_landed({VERSION_KEY: 3, TOKEN_KEY: "now"}, from_version=0, token="now") is False


def test_a_record_is_read_as_a_point_with_its_vector_only_when_that_is_a_plain_list():
    identifier = uuid4()

    plain = _point(SimpleNamespace(id=str(identifier), payload={"a": 1}, vector=(0.5, 0.25), score=3))
    named = _point(SimpleNamespace(id=identifier, payload=None, vector={"dense": [1.0]}))
    listed = _point(SimpleNamespace(id=identifier, payload={}, vector=[0.5, 0.25], score=0.75))

    assert plain == Point(id=identifier, payload={"a": 1}, vector=None, score=3.0)
    assert isinstance(plain.score, float)
    assert named == Point(id=identifier, payload={}, vector=None, score=0.0)
    assert listed == Point(id=identifier, payload={}, vector=[0.5, 0.25], score=0.75)
