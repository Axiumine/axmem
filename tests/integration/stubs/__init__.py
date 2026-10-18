"""In-memory stub repositories and test doubles for the integration suite.

One module per domain; ``tests/integration/conftest.py`` imports these and
exposes them to the tests as pytest fixtures.
"""

from tests.integration.stubs.code_artifacts import InMemoryCodeArtifactRepository
from tests.integration.stubs.documents import InMemoryDocumentRepository
from tests.integration.stubs.doubles import CollectingEventBus, MockEmbeddingsAdapter
from tests.integration.stubs.entities import InMemoryEntityRepository
from tests.integration.stubs.files import InMemoryFileRepository
from tests.integration.stubs.memories import InMemoryMemoryRepository
from tests.integration.stubs.plans import InMemoryPlanRepository
from tests.integration.stubs.projects import InMemoryProjectRepository
from tests.integration.stubs.skills import InMemorySkillRepository
from tests.integration.stubs.tasks import InMemoryTaskRepository
from tests.integration.stubs.users import InMemoryUserRepository

__all__ = [
    "CollectingEventBus",
    "InMemoryCodeArtifactRepository",
    "InMemoryDocumentRepository",
    "InMemoryEntityRepository",
    "InMemoryFileRepository",
    "InMemoryMemoryRepository",
    "InMemoryPlanRepository",
    "InMemoryProjectRepository",
    "InMemorySkillRepository",
    "InMemoryTaskRepository",
    "InMemoryUserRepository",
    "MockEmbeddingsAdapter",
]
