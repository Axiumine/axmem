"""In-memory UserRepository stub."""

from datetime import UTC, datetime
from uuid import UUID, uuid4

from app.models.user_models import User, UserCreate, UserUpdate
from app.protocols.user_protocol import UserRepository
from tests.integration.stubs.helpers import patch_item


class InMemoryUserRepository(UserRepository):
    """In-memory implementation of UserRepository for testing"""

    def __init__(self):
        self._users: dict[UUID, User] = {}
        self._external_id_index: dict[str, UUID] = {}

    async def get_user_by_id(self, user_id: UUID) -> User | None:
        return self._users.get(user_id)

    async def get_user_by_external_id(self, external_id: str) -> User | None:
        user_id = self._external_id_index.get(external_id)
        if user_id:
            return self._users.get(user_id)
        return None

    async def create_user(self, user: UserCreate) -> User:
        user_id = uuid4()
        now = datetime.now(UTC)

        new_user = User(
            id=user_id,
            external_id=user.external_id,
            name=user.name,
            email=user.email,
            notes=user.notes,
            idp_metadata=user.idp_metadata,
            created_at=now,
            updated_at=now,
        )

        self._users[user_id] = new_user
        self._external_id_index[user.external_id] = user_id
        return new_user

    async def update_user(self, user_id: UUID, updated_user: UserUpdate) -> User | None:
        user = self._users.get(user_id)
        if not user:
            return None

        # external_id is immutable; everything else follows PATCH semantics
        update_data = updated_user.model_dump(exclude_unset=True)
        update_data.pop("external_id", None)
        return patch_item(user, update_data)
