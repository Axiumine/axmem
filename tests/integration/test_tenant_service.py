"""Integration tests for TenantService against the in-memory user repository."""
import pytest

from app.exceptions import ConflictError
from app.models.user_models import UserCreate


@pytest.mark.asyncio
async def test_first_use_creates_the_tenant(test_tenant_service):
    tenant = await test_tenant_service.get_or_create_tenant("acme")

    assert tenant.external_id == "acme"
    assert tenant.name == "acme"
    assert tenant.email == ""
    assert await test_tenant_service.user_repo.get_user_by_external_id("acme") == tenant


@pytest.mark.asyncio
async def test_later_use_returns_the_stored_record_unchanged(test_tenant_service):
    first = await test_tenant_service.get_or_create_tenant("acme")
    again = await test_tenant_service.get_or_create_tenant("acme")

    assert again == first
    assert again.updated_at == first.updated_at


@pytest.mark.asyncio
async def test_tenants_get_separate_records(test_tenant_service):
    acme = await test_tenant_service.get_or_create_tenant("acme")
    globex = await test_tenant_service.get_or_create_tenant("globex")

    assert acme.id != globex.id


@pytest.mark.asyncio
async def test_a_concurrent_creation_is_resolved_to_the_winner(test_tenant_service, monkeypatch):
    repo = test_tenant_service.user_repo
    winner = None
    original_get = repo.get_user_by_external_id

    async def lookup_then_lose_the_race(external_id):
        nonlocal winner
        result = await original_get(external_id)
        if winner is None:
            winner = await repo.create_user(UserCreate(external_id=external_id, name="w", email=""))
        return result

    monkeypatch.setattr(repo, "get_user_by_external_id", lookup_then_lose_the_race)

    tenant = await test_tenant_service.get_or_create_tenant("racing")

    assert tenant == winner


@pytest.mark.asyncio
async def test_a_creation_that_conflicts_with_a_vanished_record_fails(test_tenant_service, monkeypatch):
    repo = test_tenant_service.user_repo

    async def always_conflicts(user):
        raise ConflictError("A user with this external_id already exists")

    monkeypatch.setattr(repo, "create_user", always_conflicts)

    with pytest.raises(RuntimeError, match="Tenant could not be provisioned"):
        await test_tenant_service.get_or_create_tenant("ghost")
