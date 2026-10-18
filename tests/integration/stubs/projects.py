"""In-memory ProjectRepository stub."""

from datetime import UTC, datetime, timedelta
from uuid import UUID

from app.models.project_models import (
    Project,
    ProjectCreate,
    ProjectStatus,
    ProjectSummary,
    ProjectUpdate,
)
from app.protocols.project_protocol import ProjectRepository
from tests.integration.stubs.helpers import (
    attr_equals,
    filter_items,
    find_item,
    patch_item,
    provenance_kwargs,
    remove_item,
    require_item,
    store_item,
)


class InMemoryProjectRepository(ProjectRepository):
    """In-memory implementation of ProjectRepository for testing"""

    def __init__(self):
        self._projects: dict[
            UUID, dict[int, Project],
        ] = {}  # user_id -> {project_id -> Project}
        self._next_id = 1
        # Track memories per project for memory_count calculation
        self._project_memories: dict[
            int, set[int],
        ] = {}  # project_id -> set of memory_ids

    async def list_projects(
        self,
        user_id: UUID,
        status: ProjectStatus | None = None,
        repo_name: str | None = None,
        name: str | None = None,
    ) -> list[ProjectSummary]:
        """List projects with optional filtering"""
        projects = filter_items(
            self._projects.get(user_id, {}).values(),
            (status, attr_equals("status", status)),
            (repo_name, attr_equals("repo_name", repo_name)),
            # Case-insensitive partial match
            (name, lambda p: name.lower() in p.name.lower()),
        )

        # Newest first, as ProjectSummary rows
        projects.sort(key=lambda p: (p.created_at, p.id), reverse=True)
        return [
            ProjectSummary(
                id=p.id,
                name=p.name,
                project_type=p.project_type,
                status=p.status,
                repo_name=p.repo_name,
                last_encoding_point=p.last_encoding_point,
                memory_count=p.memory_count,
                created_at=p.created_at,
                updated_at=p.updated_at,
            )
            for p in projects
        ]

    async def get_project_by_id(self, user_id: UUID, project_id: int) -> Project | None:
        """Get single project by ID"""
        return find_item(self._projects, user_id, project_id)

    async def create_project(
        self, user_id: UUID, project_data: ProjectCreate,
    ) -> Project:
        """Create new project"""
        project_id = self._next_id
        self._next_id += 1

        now = datetime.now(UTC) + timedelta(microseconds=project_id)

        new_project = Project(
            id=project_id,
            name=project_data.name,
            description=project_data.description,
            project_type=project_data.project_type,
            status=project_data.status,
            repo_name=project_data.repo_name,
            last_encoding_point=project_data.last_encoding_point,
            notes=project_data.notes,
            **provenance_kwargs(project_data),
            memory_count=0,
            created_at=now,
            updated_at=now,
        )

        self._project_memories[project_id] = set()
        return store_item(self._projects, user_id, project_id, new_project)

    async def update_project(
        self, user_id: UUID, project_id: int, project_data: ProjectUpdate,
    ) -> Project:
        """Update existing project"""
        project = require_item(
            await self.get_project_by_id(user_id, project_id),
            f"Project with id {project_id} not found",
        )
        # PATCH semantics
        return patch_item(project, project_data.model_dump(exclude_unset=True))

    async def delete_project(self, user_id: UUID, project_id: int) -> bool:
        """Delete project"""
        deleted = remove_item(self._projects, user_id, project_id)
        if deleted:
            # Clean up memory tracking
            self._project_memories.pop(project_id, None)
        return deleted
