from typing import Protocol

from app.models.user_models import User, UserCreate


class UserRepository(Protocol):
    """Contract for the repository of tenant records"""

    async def get_user_by_external_id(self, external_id: str) -> User | None: ...
    async def create_user(self, user: UserCreate) -> User:
        """Create a user; raise ConflictError when external_id already exists."""
        ...
