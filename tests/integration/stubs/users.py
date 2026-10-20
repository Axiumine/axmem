"""In-memory UserRepository stub."""

from datetime import UTC, datetime
from uuid import UUID, uuid4

from app.exceptions import ConflictError
from app.models.user_models import User, UserCreate
from app.protocols.user_protocol import UserRepository


class InMemoryUserRepository(UserRepository):
    """In-memory implementation of UserRepository for testing"""

    def __init__(self):
        self._users: dict[UUID, User] = {}
        self._external_id_index: dict[str, UUID] = {}

    async def get_user_by_external_id(self, external_id: str) -> User | None:
        user_id = self._external_id_index.get(external_id)
        if user_id:
            return self._users.get(user_id)
        return None

    async def create_user(self, user: UserCreate) -> User:
        if user.external_id in self._external_id_index:
            raise ConflictError(f"User with external_id {user.external_id} already exists")
        user_id = uuid4()
        now = datetime.now(UTC)

        new_user = User(
            id=user_id,
            external_id=user.external_id,
            name=user.name,
            email=user.email,
            created_at=now,
            updated_at=now,
        )

        self._users[user_id] = new_user
        self._external_id_index[user.external_id] = user_id
        return new_user
