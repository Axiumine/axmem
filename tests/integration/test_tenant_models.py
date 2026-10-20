"""The tenant id: a name maps to the same id on every instance, and stored data and exports carry it.

Every Qdrant-backed test replaces ``TENANT_NAMESPACE`` with a random one, so only these tests pin the real derivation.
"""
from uuid import UUID

import pytest

from app.models import tenant_models
from app.models.tenant_models import TENANT_NAMESPACE, Tenant


def test_the_namespace_is_the_fixed_one_that_exports_depend_on():
    assert UUID("6f1d8a3c-2b47-5e90-9c1a-4d7e0b5a3f21") == TENANT_NAMESPACE


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("alice", "ffc87388-58bb-518f-b22a-99e4de23150e"),
        ("default", "60e8c4b8-1007-5c2f-a357-8bbec0404b7f"),
    ],
)
def test_a_tenant_name_always_maps_to_the_same_id(name, expected):
    assert Tenant(name).id == UUID(expected)


def test_different_names_are_different_tenants_and_the_same_name_is_one_tenant():
    assert Tenant("alice").id == Tenant("alice").id
    assert Tenant("alice").id != Tenant("Alice").id
    assert Tenant("alice").id != Tenant("bob").id


def test_the_id_follows_the_namespace_the_module_holds(monkeypatch):
    before = Tenant("alice").id
    monkeypatch.setattr(tenant_models, "TENANT_NAMESPACE", UUID(int=7))

    assert Tenant("alice").id != before
