"""How QdrantStore scopes and filters, exactly: tenants, the clauses of a where, the condition builders, the tenant stamp.

None of this needs a node: the filters are values that the store hands to the client.
"""
from datetime import UTC, datetime
from uuid import UUID

import pytest
from qdrant_client import models as qm

from app.repositories.qdrant.store import (
    SYSTEM,
    TENANT_KEY,
    QdrantStore,
    Scope,
    Where,
    any_of,
    at_least,
    below,
    equals,
    has_ids,
    scoped,
    since,
)
from tests.qdrant_store_support import TENANT


@pytest.fixture
def conditions():
    """Four different conditions, to tell where each one ended up."""
    return [equals(name, 1) for name in "wxyz"]


@pytest.fixture
def chained(conditions) -> Where:
    first, second, third, fourth = conditions
    return Where().also(first).also(second).excluding(third).either(fourth)


def test_a_scope_names_its_tenant_as_text_and_the_system_scope_names_none():
    assert Scope.of(TENANT) == Scope(str(TENANT))
    assert Scope.of(TENANT).tenant_id == "00000000-0000-4000-8000-0000000000aa"
    assert SYSTEM.tenant_id is None


def test_the_conditions_of_a_where_are_kept_apart_and_in_the_order_they_were_added(conditions):
    first, second, third, fourth = conditions
    base = Where()

    chained_where = base.also(first).also(second).excluding(third).either(fourth)

    assert (chained_where.must, chained_where.must_not, chained_where.should) == ((first, second), (third,), (fourth,))
    assert base.also(first, second).must == (first, second)
    assert base == Where((), (), ())


def test_conditions_added_together_to_a_clause_follow_the_ones_it_holds_in_the_order_given(conditions, chained):
    first, second, third, fourth = conditions

    assert chained.excluding(first, second).must_not == (third, first, second)
    assert chained.either(first, second).should == (fourth, first, second)


def test_a_condition_added_to_one_clause_leaves_the_other_clauses_as_they_were(conditions, chained):
    first, second, third, fourth = conditions

    assert chained.also(third).must_not == (third,)
    assert chained.also(third).should == (fourth,)
    assert chained.excluding(first).should == (fourth,)
    assert chained.either(third).must == (first, second)


def test_each_condition_builder_makes_the_condition_of_its_kind():
    moment = datetime(2026, 3, 4, 5, 6, 7, tzinfo=UTC)
    ids = [UUID(int=1), UUID(int=2)]

    assert equals("is_obsolete", False) == qm.FieldCondition(key="is_obsolete", match=qm.MatchValue(value=False))
    assert equals("project", "p") == qm.FieldCondition(key="project", match=qm.MatchValue(value="p"))
    assert any_of("tags", (tag for tag in ["a", "b"])) == qm.FieldCondition(key="tags", match=qm.MatchAny(any=["a", "b"]))
    assert has_ids(iter(ids)) == qm.HasIdCondition(has_id=[str(UUID(int=1)), str(UUID(int=2))])
    assert at_least("importance", 7) == qm.FieldCondition(key="importance", range=qm.Range(gte=7))
    assert below("version", 3) == qm.FieldCondition(key="version", range=qm.Range(lt=3))
    assert since("created_at", moment) == qm.FieldCondition(key="created_at", range=qm.DatetimeRange(gte=moment))


def test_a_filter_puts_the_tenant_first_and_leaves_an_empty_clause_out():
    condition, other, third = equals("a", 1), equals("b", 2), equals("c", 3)
    tenant = equals(TENANT_KEY, str(TENANT))

    assert scoped(Scope.of(TENANT)) == qm.Filter(must=[tenant], must_not=None, should=None)
    assert scoped(Scope.of(TENANT), Where().also(condition).excluding(other).either(third)) == qm.Filter(
        must=[tenant, condition], must_not=[other], should=[third],
    )
    assert scoped(SYSTEM, Where().also(condition)) == qm.Filter(must=[condition], must_not=None, should=None)
    assert scoped(SYSTEM, Where().excluding(other)) == qm.Filter(must=None, must_not=[other], should=None)
    assert scoped(SYSTEM, Where().either(third)) == qm.Filter(must=None, must_not=None, should=[third])
    assert scoped(SYSTEM) == qm.Filter(must=None, must_not=None, should=None)


def test_a_system_write_is_refused_without_a_tenant_and_a_tenant_scope_stamps_its_own():
    payload = {"title": "t", TENANT_KEY: str(TENANT)}

    assert QdrantStore._tenant_payload(SYSTEM, payload) is payload
    stamped = QdrantStore._tenant_payload(Scope("other"), payload)
    assert stamped == {"title": "t", TENANT_KEY: "other"}
    assert payload[TENANT_KEY] == str(TENANT)
    for payload_without_tenant in ({"title": "t"}, {"title": "t", TENANT_KEY: ""}, {"title": "t", TENANT_KEY: None}):
        with pytest.raises(ValueError) as refused:
            QdrantStore._tenant_payload(SYSTEM, payload_without_tenant)
        assert str(refused.value) == "a point written in the system scope must carry its tenant_id"
