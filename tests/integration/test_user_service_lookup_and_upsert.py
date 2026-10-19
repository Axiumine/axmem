"""UserService lookups by id and the no-op / provisioning branches of ``update_user``."""

from uuid import uuid4

from app.models.user_models import UserCreate, UserUpdate


async def test_user_is_found_by_its_internal_id(test_user_service):
    created = await test_user_service.get_or_create_user(
        UserCreate(external_id="lookup-1", name="Lookup", email="lookup@example.com"),
    )

    found = await test_user_service.get_user_by_id(created.id)

    assert found is not None
    assert (found.id, found.external_id, found.email) == (created.id, "lookup-1", "lookup@example.com")


async def test_unknown_internal_id_finds_no_user(test_user_service):
    assert await test_user_service.get_user_by_id(uuid4()) is None


async def test_update_user_with_unchanged_details_keeps_the_stored_record(test_user_service):
    created = await test_user_service.get_or_create_user(
        UserCreate(external_id="same-1", name="Same", email="same@example.com"),
    )

    result = await test_user_service.update_user(
        UserUpdate(external_id="same-1", name="Same", email="same@example.com"),
    )

    assert result.id == created.id
    assert result.updated_at == created.updated_at


async def test_update_user_provisions_a_user_that_does_not_exist_yet(test_user_service):
    result = await test_user_service.update_user(
        UserUpdate(external_id="fresh-1", name="Fresh", email="fresh@example.com"),
    )

    stored = await test_user_service.get_user_by_id(result.id)
    assert (stored.external_id, stored.name, stored.email) == ("fresh-1", "Fresh", "fresh@example.com")
