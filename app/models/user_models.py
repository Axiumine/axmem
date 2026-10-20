from datetime import UTC, datetime
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field

# users.external_id, users.name and users.email are VARCHAR(255). SQLite does not enforce
# that, PostgreSQL does, so bound new input here to behave the same on both backends.
IDENTITY_MAX_LENGTH = 255


class UserCreate(BaseModel):
    """Storage record of a tenant: ``external_id`` is the tenant name taken from the credential.

    ``name`` repeats the tenant name and ``email`` is empty; the columns stay because the
    storage layer needs them, and nothing reads them back.
    """

    external_id: str = Field(max_length=IDENTITY_MAX_LENGTH)
    name: str = Field(max_length=IDENTITY_MAX_LENGTH)
    email: str = Field(max_length=IDENTITY_MAX_LENGTH)


class User(UserCreate):
    # Stored rows are read back through this model. SQLite may already hold values longer
    # than the column size, so only new input (UserCreate) is length-bounded.
    external_id: str
    name: str
    email: str
    id: UUID = Field(default_factory=lambda: uuid4())
    updated_at: datetime = Field(default_factory=lambda: datetime.now(tz=UTC))
    created_at: datetime = Field(default_factory=lambda: datetime.now(tz=UTC), frozen=True)

    model_config = ConfigDict(from_attributes=True)
