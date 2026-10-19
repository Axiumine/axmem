"""Shared setup for the PostgreSQL repository-level e2e modules.

``build_world`` registers two users (``owner`` and ``stranger``) and wires the real
PostgreSQL repositories to the session database, so a test can create rows through the
repositories themselves and read the results back through another repository call.
Assertions stay in the tests.
"""
import logging
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from uuid import UUID

from app.config.settings import settings
from app.models.code_artifact_models import CodeArtifactCreate
from app.models.document_models import DocumentCreate
from app.models.entity_models import Entity, EntityCreate, EntityType
from app.models.memory_models import Memory, MemoryCreate
from app.models.project_models import ProjectCreate, ProjectType
from app.models.user_models import UserCreate
from app.repositories.postgres.code_artifact_repository import (
    PostgresCodeArtifactRepository,
)
from app.repositories.postgres.document_repository import PostgresDocumentRepository
from app.repositories.postgres.entity_repository import PostgresEntityRepository
from app.repositories.postgres.memory_repository import PostgresMemoryRepository
from app.repositories.postgres.postgres_adapter import PostgresDatabaseAdapter
from app.repositories.postgres.project_repository import PostgresProjectRepository
from app.repositories.postgres.user_repository import PostgresUserRepository

# Fits the INTEGER id columns and is never reached by the sequences of a test run
UNKNOWN_ID = 2_000_000_000


@contextmanager
def captured_logger(
    caplog, logger_name: str, level: int = logging.INFO,
) -> Iterator[list[logging.LogRecord]]:
    """Records ``logger_name`` emits inside the block, even if Alembic disabled that logger."""
    target = logging.getLogger(logger_name)
    was_disabled = target.disabled
    target.disabled = False
    caplog.clear()
    caplog.set_level(level, logger=logger_name)
    records: list[logging.LogRecord] = []
    try:
        yield records
    finally:
        records.extend(r for r in caplog.records if r.name == logger_name)
        target.disabled = was_disabled


class ConstantEmbeddings:
    """Embedding stand-in: the same vector for any text, in the configured width."""

    async def generate_embedding(self, text: str) -> list[float]:
        return [0.01] * settings.EMBEDDING_DIMENSIONS


@dataclass
class RepositoryWorld:
    """Two users and one real repository per aggregate, all on the session database."""

    db_adapter: PostgresDatabaseAdapter
    owner: UUID
    stranger: UUID
    entities: PostgresEntityRepository
    memories: PostgresMemoryRepository
    projects: PostgresProjectRepository
    code_artifacts: PostgresCodeArtifactRepository
    documents: PostgresDocumentRepository


async def build_world(db_adapter: PostgresDatabaseAdapter) -> RepositoryWorld:
    users = PostgresUserRepository(db_adapter)
    owner = await users.create_user(
        UserCreate(external_id="owner-repo-e2e", name="Owner", email="owner@example.com"),
    )
    stranger = await users.create_user(
        UserCreate(external_id="stranger-repo-e2e", name="Stranger", email="stranger@example.com"),
    )
    embeddings = ConstantEmbeddings()
    return RepositoryWorld(
        db_adapter=db_adapter,
        owner=owner.id,
        stranger=stranger.id,
        entities=PostgresEntityRepository(db_adapter),
        memories=PostgresMemoryRepository(db_adapter, embeddings),
        projects=PostgresProjectRepository(db_adapter),
        code_artifacts=PostgresCodeArtifactRepository(db_adapter),
        documents=PostgresDocumentRepository(db_adapter),
    )


async def new_entity(world: RepositoryWorld, user_id: UUID, name: str = "Acme") -> Entity:
    return await world.entities.create_entity(
        user_id, EntityCreate(name=name, entity_type=EntityType.ORGANIZATION),
    )


async def new_memory(
    world: RepositoryWorld, user_id: UUID, title: str = "A memory", **fields,
) -> Memory:
    return await world.memories.create_memory(
        user_id,
        MemoryCreate(
            title=title,
            content=fields.pop("content", f"Content of {title}"),
            context=fields.pop("context", "repository e2e"),
            keywords=fields.pop("keywords", ["repo"]),
            tags=fields.pop("tags", ["repo"]),
            importance=fields.pop("importance", 7),
            **fields,
        ),
    )


async def new_project(world: RepositoryWorld, user_id: UUID, name: str = "Project") -> int:
    project = await world.projects.create_project(
        user_id,
        ProjectCreate(name=name, description="d", project_type=ProjectType.DEVELOPMENT),
    )
    return project.id


async def new_code_artifact(world: RepositoryWorld, user_id: UUID) -> int:
    created = await world.code_artifacts.create_code_artifact(
        user_id,
        CodeArtifactCreate(title="t", description="d", code="print(1)", language="python"),
    )
    return created.id


async def new_document(world: RepositoryWorld, user_id: UUID) -> int:
    created = await world.documents.create_document(
        user_id, DocumentCreate(title="t", description="d", content="body"),
    )
    return created.id
