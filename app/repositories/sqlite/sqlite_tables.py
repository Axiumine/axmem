"""SQLAlchemy ORM Models for SQLite database

Key differences from Postgres:
- UUID stored as String (TEXT in SQLite)
- ARRAY(String) replaced with JSON (stored as TEXT, serialized lists)
- JSONB replaced with JSON
- Vector embeddings stored in separate vec_memories virtual table
- No GIN/HNSW indexes (Postgres-specific)
- Relationships and structure identical to Postgres
"""
from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Table,
    Text,
)
from sqlalchemy.engine import Dialect
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy.types import TypeDecorator


class UTCDateTime(TypeDecorator[datetime]):
    """Timezone-aware UTC datetime for SQLite.

    SQLite has no timezone type: SQLAlchemy stores ``DateTime(timezone=True)`` as naive
    wall-clock text and returns naive datetimes, so offsets were dropped on write and
    timestamps came back without tzinfo. This type converts aware values to UTC when
    binding (naive values are taken to be UTC, as before) and re-attaches UTC on read,
    matching PostgreSQL ``timestamptz``.
    """

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        if value is not None and value.utcoffset() is not None:
            return value.astimezone(UTC)
        return value

    def process_result_value(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        if value is not None and value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value


class Base(DeclarativeBase):
    """Base Class for all ORM models"""



memory_project_association = Table(
    "memory_project_association",
    Base.metadata,
    Column("memory_id", Integer, ForeignKey("memories.id", ondelete="CASCADE"), primary_key=True),
    Column("project_id", Integer, ForeignKey("projects.id", ondelete="CASCADE"), primary_key=True),
)


class UsersTable(Base):
    """User Table Model
    """

    __tablename__ = "users"
    # UUID stored as String in SQLite
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    external_id: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(255))
    email: Mapped[str] = mapped_column(String(255))

    # Meta Data - JSONB becomes JSON
    idp_metadata: Mapped[dict] = mapped_column(JSON, nullable=True, default=dict)
    notes: Mapped[str] = mapped_column(Text, nullable=True)

    # Timestamps
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime(),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), default=lambda: datetime.now(UTC), nullable=False,
    )

    # Relationships
    memories: Mapped[list[MemoryTable]] = relationship(
        "MemoryTable", back_populates="user", cascade="all, delete-orphan",
    )
    projects: Mapped[list[ProjectsTable]] = relationship(
        "ProjectsTable", back_populates="user", cascade="all, delete-orphan",
    )


class MemoryTable(Base):
    """Memory Table Model

    Note: Embeddings stored in separate vec_memories virtual table (not in this table).
    This table references the memory_id which links to vec_memories.
    """

    __tablename__ = "memories"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)

    # Memory Content
    title: Mapped[str] = mapped_column(Text, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    context: Mapped[str] = mapped_column(Text, nullable=False)

    # ARRAY(String) replaced with JSON - will store as ["keyword1", "keyword2"]
    keywords: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    tags: Mapped[list[str]] = mapped_column(JSON, nullable=False)

    # Meta Data
    importance: Mapped[int] = mapped_column(Integer, nullable=False)
    # Note: embedding is NOT stored here - it's in vec_memories virtual table

    # Provenance tracking (optional) - for tracing AI-generated content
    source_repo: Mapped[str] = mapped_column(Text, nullable=True)
    source_files: Mapped[list[str]] = mapped_column(JSON, nullable=True)
    source_url: Mapped[str] = mapped_column(Text, nullable=True)
    confidence: Mapped[float] = mapped_column(nullable=True)
    encoding_agent: Mapped[str] = mapped_column(Text, nullable=True)
    encoding_version: Mapped[str] = mapped_column(Text, nullable=True)
    agent_id: Mapped[str] = mapped_column(Text, nullable=True)
    agent_version: Mapped[str] = mapped_column(Text, nullable=True)
    agent_model: Mapped[str] = mapped_column(Text, nullable=True)

    # Lifecycle Management
    is_obsolete: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    obsolete_reason: Mapped[str] = mapped_column(Text, nullable=True)
    superseded_by: Mapped[int] = mapped_column(Integer, ForeignKey("memories.id", ondelete="SET NULL"), nullable=True)
    obsoleted_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=True)

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), default=lambda: datetime.now(UTC), nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime(),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
        nullable=False,
    )

    # Relationships
    user: Mapped[UsersTable] = relationship("UsersTable", back_populates="memories")
    projects: Mapped[list[ProjectsTable]] = relationship(
        "ProjectsTable",
        secondary=memory_project_association,
        back_populates="memories",
    )

    linked_memories: Mapped[list[MemoryTable]] = relationship(
        "MemoryTable",
        secondary="memory_links",
        primaryjoin="MemoryTable.id==MemoryLinkTable.source_id",
        secondaryjoin="MemoryTable.id==MemoryLinkTable.target_id",
        back_populates="linking_memories",
    )

    linking_memories: Mapped[list[MemoryTable]] = relationship(
        "MemoryTable",
        secondary="memory_links",
        primaryjoin="MemoryTable.id==MemoryLinkTable.target_id",
        secondaryjoin="MemoryTable.id==MemoryLinkTable.source_id",
        back_populates="linked_memories",
        viewonly=True,
    )

    @property
    def linked_memory_ids(self) -> list[int]:
        """Compute linked memory IDs from bidirectional relationships.

        Combines IDs from both directions since links are bidirectional:
        - linked_memories: where this memory is the source
        - linking_memories: where this memory is the target

        Returns:
            List of linked memory IDs, or empty list if relationships not loaded
        """
        from sqlalchemy import inspect
        from sqlalchemy.orm.attributes import NO_VALUE

        # Check if relationships are loaded to avoid lazy-loading in async context
        insp = inspect(self)
        result = []

        # Only access if already loaded (not NO_VALUE)
        if insp.attrs.linked_memories.loaded_value is not NO_VALUE:
            result.extend([m.id for m in self.linked_memories])

        if insp.attrs.linking_memories.loaded_value is not NO_VALUE:
            result.extend([m.id for m in self.linking_memories])

        return result

    @property
    def project(self) -> str | None:
        """Scope label of the memory: the name of its first project, or None.

        A memory carries one scope. Rows written before scopes were single-valued can
        hold several project associations; the oldest one is the scope that is reported.

        Returns:
            The project name, or None when there is none or the relationship is not loaded
        """
        from sqlalchemy import inspect
        from sqlalchemy.orm.attributes import NO_VALUE

        insp = inspect(self)
        if insp.attrs.projects.loaded_value is not NO_VALUE and self.projects:
            return min(self.projects, key=lambda scope: scope.id).name
        return None

    __table_args__ = (
        Index("ix_memories_user_id", "user_id"),
        Index("ix_memories_importance", "importance"),
        # No GIN indexes - SQLite doesn't support these
        # JSON columns can be indexed but differently
        Index("ix_memories_is_obsolete", "is_obsolete"),
        Index("ix_memories_superseded_by", "superseded_by"),
        Index("ix_memories_confidence", "confidence"),
    )


class MemoryLinkTable(Base):
    """Bidirectional links table for memories
    """

    __tablename__ = "memory_links"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    source_id: Mapped[int] = mapped_column(Integer, ForeignKey("memories.id", ondelete="CASCADE"), nullable=False)
    target_id: Mapped[int] = mapped_column(Integer, ForeignKey("memories.id", ondelete="CASCADE"), nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime(),
        default=lambda: datetime.now(UTC),
        nullable=False,
    )

    # Ensure unique bidirectional links (prevent duplicates)
    __table_args__ = (
        Index("ix_memory_links_source_target", "source_id", "target_id", unique=True),
        Index("ix_memory_links_target_source", "target_id", "source_id"),
    )


class ProjectsTable(Base):
    """Project meta data for organizing memories
    """

    __tablename__ = "projects"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)

    # Project information
    name: Mapped[str] = mapped_column(String(500), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    project_type: Mapped[str] = mapped_column(String(50), nullable=True)  # TODO: create a proper enum for this
    status: Mapped[str] = mapped_column(String(50), default="active", nullable=False)  # TODO: create a proper enum
    repo_name: Mapped[str] = mapped_column(String(255), nullable=True)
    last_encoding_point: Mapped[str] = mapped_column(String(255), nullable=True)
    notes: Mapped[str] = mapped_column(Text, nullable=True)

    # Provenance tracking (optional)
    source_repo: Mapped[str] = mapped_column(Text, nullable=True)
    source_files: Mapped[list[str]] = mapped_column(JSON, nullable=True)
    source_url: Mapped[str] = mapped_column(Text, nullable=True)
    confidence: Mapped[float] = mapped_column(nullable=True)
    encoding_agent: Mapped[str] = mapped_column(Text, nullable=True)
    encoding_version: Mapped[str] = mapped_column(Text, nullable=True)
    agent_id: Mapped[str] = mapped_column(Text, nullable=True)
    agent_version: Mapped[str] = mapped_column(Text, nullable=True)
    agent_model: Mapped[str] = mapped_column(Text, nullable=True)

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime(),
        default=lambda: datetime.now(UTC),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime(),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
        nullable=False,
    )

    # Relationships
    user: Mapped[UsersTable] = relationship("UsersTable", back_populates="projects")
    memories: Mapped[list[MemoryTable]] = relationship(
        "MemoryTable",
        secondary=memory_project_association,
        back_populates="projects",
    )

    __table_args__ = (
        Index("ix_projects_user_id", "user_id"),
        Index("ix_projects_status", "status"),
    )
