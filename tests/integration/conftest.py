"""Integration test fixtures with in-memory stubs (no real database required)

The stub repositories and test doubles live in ``tests/integration/stubs/`` (one
module per domain); this module imports them and exposes the fixtures the tests use.
"""

import pytest

from app.services.code_artifact_service import CodeArtifactService
from app.services.document_service import DocumentService
from app.services.entity_service import EntityService
from app.services.file_service import FileService
from app.services.memory_service import MemoryService
from app.services.plan_service import PlanService
from app.services.project_service import ProjectService
from app.services.skill_service import SkillService
from app.services.task_service import TaskService
from app.services.user_service import UserService
from tests.integration.stubs import (
    CollectingEventBus,
    InMemoryCodeArtifactRepository,
    InMemoryDocumentRepository,
    InMemoryEntityRepository,
    InMemoryFileRepository,
    InMemoryMemoryRepository,
    InMemoryPlanRepository,
    InMemoryProjectRepository,
    InMemorySkillRepository,
    InMemoryTaskRepository,
    InMemoryUserRepository,
    MockEmbeddingsAdapter,
)


@pytest.fixture
def clean_test_data():
    """Fixture that provides a clean slate for each test"""
    # Setup: nothing needed
    return
    # Teardown: nothing needed (new instance per test)


@pytest.fixture
def mock_user_repository():
    """Provides an in-memory user repository"""
    return InMemoryUserRepository()


@pytest.fixture
def test_user_service(mock_user_repository):
    """Provides a UserService with in-memory repository"""
    return UserService(mock_user_repository)


# ============ Memory Testing Fixtures ============


@pytest.fixture
def mock_embeddings_adapter():
    """Provides a mock embeddings adapter"""
    return MockEmbeddingsAdapter(dimensions=384)


@pytest.fixture
def mock_memory_repository():
    """Provides an in-memory memory repository"""
    return InMemoryMemoryRepository()


@pytest.fixture
def test_memory_service(mock_memory_repository):
    """Provides a MemoryService with in-memory repository"""
    return MemoryService(mock_memory_repository)


@pytest.fixture
def test_memory_service_with_event_bus(mock_memory_repository):
    """Provides a MemoryService with event bus for testing event emission."""
    event_bus = CollectingEventBus()
    service = MemoryService(mock_memory_repository, event_bus=event_bus)
    return service, event_bus


@pytest.fixture
def test_memory_service_with_access_tracking(mock_memory_repository):
    """MemoryService + EventBus with access-tracking handler subscribed."""
    event_bus = CollectingEventBus()
    service = MemoryService(mock_memory_repository, event_bus=event_bus)
    service.register_access_tracking_handlers(event_bus)
    return service, event_bus


# ============ Project Testing Fixtures ============


@pytest.fixture
def mock_project_repository():
    """Provides an in-memory project repository"""
    return InMemoryProjectRepository()


@pytest.fixture
def test_project_service(mock_project_repository):
    """Provides a ProjectService with in-memory repository"""
    return ProjectService(mock_project_repository)


@pytest.fixture
def test_project_service_with_event_bus(mock_project_repository):
    """Provides a ProjectService with event bus for testing event emission."""
    event_bus = CollectingEventBus()
    service = ProjectService(mock_project_repository, event_bus=event_bus)
    return service, event_bus


# ============ Code Artifact Testing Fixtures ============


@pytest.fixture
def mock_code_artifact_repository():
    return InMemoryCodeArtifactRepository()


@pytest.fixture
def test_code_artifact_service(mock_code_artifact_repository):
    return CodeArtifactService(mock_code_artifact_repository)


@pytest.fixture
def test_code_artifact_service_with_event_bus(mock_code_artifact_repository):
    """Provides a CodeArtifactService with event bus for testing event emission."""
    event_bus = CollectingEventBus()
    service = CodeArtifactService(mock_code_artifact_repository, event_bus=event_bus)
    return service, event_bus


# ============ Document Testing Fixtures ============


@pytest.fixture
def mock_document_repository():
    return InMemoryDocumentRepository()


@pytest.fixture
def test_document_service(mock_document_repository):
    return DocumentService(mock_document_repository)


@pytest.fixture
def test_document_service_with_event_bus(mock_document_repository):
    """Provides a DocumentService with event bus for testing event emission."""
    event_bus = CollectingEventBus()
    service = DocumentService(mock_document_repository, event_bus=event_bus)
    return service, event_bus


# ============ File Testing Fixtures ============


@pytest.fixture
def mock_file_repository():
    return InMemoryFileRepository()


@pytest.fixture
def test_file_service(mock_file_repository):
    return FileService(mock_file_repository)


@pytest.fixture
def test_file_service_with_event_bus(mock_file_repository):
    """Provides a FileService with event bus for testing event emission."""
    event_bus = CollectingEventBus()
    service = FileService(mock_file_repository, event_bus=event_bus)
    return service, event_bus


# ============ Entity Testing Fixtures ============


@pytest.fixture
def mock_entity_repository():
    return InMemoryEntityRepository()


@pytest.fixture
def test_entity_service(mock_entity_repository):
    return EntityService(mock_entity_repository)


@pytest.fixture
def test_entity_service_with_event_bus(mock_entity_repository):
    """Provides an EntityService with event bus for testing event emission."""
    event_bus = CollectingEventBus()
    service = EntityService(mock_entity_repository, event_bus=event_bus)
    return service, event_bus


# ============ Plan Testing Fixtures ============


@pytest.fixture
def mock_plan_repository():
    """Provides an in-memory plan repository"""
    return InMemoryPlanRepository()


@pytest.fixture
def test_plan_service(mock_plan_repository):
    """Provides a PlanService with in-memory repository"""
    repo = mock_plan_repository
    service = PlanService(repo, event_bus=None)
    return service


# ============ Task Testing Fixtures ============


@pytest.fixture
def mock_task_repository():
    """Provides an in-memory task repository"""
    return InMemoryTaskRepository()


@pytest.fixture
def test_task_service(test_plan_service, mock_task_repository):
    """Provides a TaskService with in-memory repository and linked PlanService."""
    repo = mock_task_repository
    service = TaskService(repo, plan_service=test_plan_service, event_bus=None)
    return service, test_plan_service


# ============ Skill Testing Fixtures ============


@pytest.fixture
def mock_skill_repository():
    return InMemorySkillRepository()


@pytest.fixture
def test_skill_service(mock_skill_repository):
    return SkillService(mock_skill_repository, event_bus=None)


# =============================================================================
# Real-runtime settings fixture (bootstrap factory / CLI executor tests)
# =============================================================================

_SQLITE_RUNTIME_FIELDS = [
    "DATABASE",
    "SQLITE_MEMORY",
    "EMBEDDING_PROVIDER",
    "RERANKING_ENABLED",
    "FORGETFUL_SCOPES",
    "SKILLS_ENABLED",
    "FILES_ENABLED",
    "PLANNING_ENABLED",
    "ACTIVITY_ENABLED",
]


@pytest.fixture
def sqlite_runtime_settings():
    """Point the composition root at in-memory SQLite with deterministic feature flags.

    Reranking is disabled to skip the cross-encoder model load; reranker wiring is
    exercised by the e2e_sqlite suite.
    """
    from app.config.settings import settings

    original = {name: getattr(settings, name) for name in _SQLITE_RUNTIME_FIELDS}
    settings.DATABASE = "SQLite"
    settings.SQLITE_MEMORY = True
    settings.EMBEDDING_PROVIDER = "FastEmbed"
    settings.RERANKING_ENABLED = False
    settings.FORGETFUL_SCOPES = "*"
    settings.SKILLS_ENABLED = False
    settings.FILES_ENABLED = False
    settings.PLANNING_ENABLED = False
    settings.ACTIVITY_ENABLED = False
    yield settings
    for name, value in original.items():
        setattr(settings, name, value)
