"""User repository for postgres data access operations
"""
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.exceptions import ConflictError
from app.models.user_models import User, UserCreate
from app.repositories.postgres.postgres_adapter import PostgresDatabaseAdapter
from app.repositories.postgres.postgres_errors import violated_constraint
from app.repositories.postgres.postgres_tables import UsersTable


class PostgresUserRepository:
    """Repository or User entity operations in Postgres
    """

    def __init__(self, db_adapter: PostgresDatabaseAdapter):
        self.db_adapter = db_adapter


    async def get_user_by_external_id(self, external_id: str) -> User | None:
        """Gets a user by their external id

        Args:
            user_id: external_id string 
        
        Returns:
            User object or None if not found
        """
        async with self.db_adapter.system_session() as session:
            result = await session.execute(select(UsersTable).where(UsersTable.external_id==external_id))
            user_orm = result.scalars().first()
            if user_orm:
                return User.model_validate(user_orm)
            return None


    async def create_user(self, user: UserCreate) -> User:
        """Creates a new entry in the user entity

        Args:
            User Create: user create object 
            
        Returns:
            User object
        """
        try:
            async with self.db_adapter.system_session() as session:
                new_user = UsersTable(**user.model_dump())
                session.add(new_user)
                await session.flush()
                await session.refresh(new_user)
                return User.model_validate(new_user)
        except IntegrityError as exc:
            if violated_constraint(exc) == "ix_users_external_id":
                raise ConflictError("A user with this external_id already exists") from exc
            raise
