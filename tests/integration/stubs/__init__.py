"""In-memory stub repositories for the integration suite.

One module per domain; ``tests/integration/conftest.py`` imports these and
exposes them to the tests as pytest fixtures.
"""

from tests.integration.stubs.memories import InMemoryMemoryRepository
from tests.integration.stubs.users import InMemoryUserRepository

__all__ = [
    "InMemoryMemoryRepository",
    "InMemoryUserRepository",
]
