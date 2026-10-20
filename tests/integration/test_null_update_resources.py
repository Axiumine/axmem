"""Integration tests: an explicit null in an update means "unchanged".

Every ``*Update`` model documents "None/omitted fields remain unchanged".
The MCP adapters strip ``None`` before building the model, but REST clients
(``PUT {"tags": null}``) and direct callers can hand a literal null to the
model, where it used to count as an explicitly set field. It was then written
to the row (NOT NULL violations, or JSON null in list columns that made the
record unreadable and undeletable) and ignored by the no-op detection.

These tests run the services against the in-memory stub repositories, which
apply updates the same way as the real ones (``model_dump(exclude_unset=True)``).
"""
import asyncio
from dataclasses import dataclass
from typing import Any

import pytest

from app.models.project_models import ProjectCreate, ProjectType, ProjectUpdate
from app.models.user_models import User


@pytest.fixture
def test_user():
    return User(external_id="null-update", name="Null Update", email="null@example.com")


@dataclass
class Case:
    """One resource under test: how to create, read and update it."""

    name: str
    fixture: str
    model: type
    create: Any
    update: Any
    get: Any
    # Every field of the update model that must not accept a null.
    null_fields: dict[str, None]
    # A change that really modifies the stored record.
    change: dict[str, Any]
    # A payload that repeats what is already stored (a no-op).
    same: dict[str, Any]

    def nulls_besides_change(self) -> dict[str, None]:
        """Null payload for every field the real change does not set."""
        return {field: None for field in self.null_fields if field not in self.change}


CASES = [
    Case(
        name="project",
        fixture="test_project_service",
        model=ProjectUpdate,
        create=lambda s, u: s.create_project(
            u,
            ProjectCreate(
                name="Proj",
                description="A project",
                project_type=ProjectType.DEVELOPMENT,
                notes="keep me",
            ),
        ),
        update=lambda s, u, i, d: s.update_project(u, i, d),
        get=lambda s, u, i: s.get_project(u, i),
        null_fields={
            "name": None,
            "description": None,
            "project_type": None,
            "status": None,
            "notes": None,
            "repo_name": None,
        },
        change={"name": "Renamed"},
        same={"name": "Proj", "notes": "keep me"},
    ),
]


def _ids(cases):
    return [case.name for case in cases]


# ---------------------------------------------------------------------------
# Update models: a literal null is not a change request
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("case", CASES, ids=_ids(CASES))
def test_update_model_ignores_explicit_null(case):
    update = case.model(**case.null_fields)

    assert update.model_fields_set == set()
    assert update.model_dump(exclude_unset=True) == {}


@pytest.mark.parametrize("case", CASES, ids=_ids(CASES))
def test_update_model_keeps_real_values_next_to_nulls(case):
    update = case.model(**case.nulls_besides_change(), **case.change)

    assert update.model_fields_set == set(case.change)
    assert update.model_dump(exclude_unset=True) == case.change


@pytest.mark.parametrize(
    ("model", "field"),
    [
        (ProjectUpdate, "notes"),
        (ProjectUpdate, "repo_name"),
        (ProjectUpdate, "last_encoding_point"),
    ],
)
def test_empty_string_still_clears_optional_fields(model, field):
    """The documented way to clear an optional field is the empty string."""
    update = model(**{field: ""})

    assert field in update.model_fields_set
    assert update.model_dump(exclude_unset=True) == {field: None}


# ---------------------------------------------------------------------------
# Services: nothing is written
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("case", CASES, ids=_ids(CASES))
async def test_service_update_with_only_nulls_leaves_record_untouched(case, test_user, request):
    service = request.getfixturevalue(case.fixture)
    created = await case.create(service, test_user.id)
    before = (await case.get(service, test_user.id, created.id)).model_dump(mode="json")
    await asyncio.sleep(0.002)

    result = await case.update(service, test_user.id, created.id, case.model(**case.null_fields))

    after = (await case.get(service, test_user.id, created.id)).model_dump(mode="json")
    assert after == before
    assert result.model_dump(mode="json") == before


@pytest.mark.parametrize("case", CASES, ids=_ids(CASES))
async def test_service_update_applies_value_and_ignores_null_sibling(case, test_user, request):
    service = request.getfixturevalue(case.fixture)
    created = await case.create(service, test_user.id)
    before = (await case.get(service, test_user.id, created.id)).model_dump(mode="json")

    await case.update(service, test_user.id, created.id, case.model(**case.nulls_besides_change(), **case.change))

    after = (await case.get(service, test_user.id, created.id)).model_dump(mode="json")
    for field, value in after.items():
        if field in case.change:
            assert value == case.change[field]
        elif field != "updated_at":
            assert value == before[field], f"{field} changed"


# ---------------------------------------------------------------------------
# Services: an update that changes nothing is a no-op
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("case", CASES, ids=_ids(CASES))
async def test_service_noop_update_keeps_updated_at(case, test_user, request):
    service = request.getfixturevalue(case.fixture)
    created = await case.create(service, test_user.id)
    updated_at = created.updated_at
    await asyncio.sleep(0.002)

    result = await case.update(service, test_user.id, created.id, case.model(**case.same))

    assert result.updated_at == updated_at
    assert (await case.get(service, test_user.id, created.id)).updated_at == updated_at


@pytest.mark.parametrize("case", CASES, ids=_ids(CASES))
async def test_service_empty_update_keeps_updated_at(case, test_user, request):
    service = request.getfixturevalue(case.fixture)
    created = await case.create(service, test_user.id)
    updated_at = created.updated_at
    await asyncio.sleep(0.002)

    result = await case.update(service, test_user.id, created.id, case.model())

    assert result.updated_at == updated_at
