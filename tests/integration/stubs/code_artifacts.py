"""In-memory CodeArtifactRepository stub."""

from datetime import UTC, datetime
from uuid import UUID

from app.models.code_artifact_models import (
    CodeArtifact,
    CodeArtifactCreate,
    CodeArtifactSummary,
    CodeArtifactUpdate,
)
from app.protocols.code_artifact_protocol import CodeArtifactRepository
from tests.integration.stubs.helpers import (
    attr_equals,
    filter_items,
    find_item,
    newest_first,
    patch_item,
    provenance_kwargs,
    remove_item,
    require_item,
    shares_any,
    store_item,
)


class InMemoryCodeArtifactRepository(CodeArtifactRepository):
    """In-memory implementation of CodeArtifactRepository for testing"""

    def __init__(self):
        self._artifacts: dict[UUID, dict[int, CodeArtifact]] = {}
        self._next_id = 1

    async def create_code_artifact(
        self, user_id: UUID, artifact_data: CodeArtifactCreate,
    ) -> CodeArtifact:
        artifact_id = self._next_id
        self._next_id += 1
        now = datetime.now(UTC)

        new_artifact = CodeArtifact(
            id=artifact_id,
            title=artifact_data.title,
            description=artifact_data.description,
            code=artifact_data.code,
            language=artifact_data.language.lower(),
            tags=artifact_data.tags,
            project_id=None,
            created_at=now,
            updated_at=now,
            **provenance_kwargs(artifact_data),
        )
        return store_item(self._artifacts, user_id, artifact_id, new_artifact)

    async def get_code_artifact_by_id(
        self, user_id: UUID, artifact_id: int,
    ) -> CodeArtifact | None:
        return find_item(self._artifacts, user_id, artifact_id)

    async def list_code_artifacts(
        self,
        user_id: UUID,
        project_id: int | None = None,
        language: str | None = None,
        tags: list[str] | None = None,
    ) -> list[CodeArtifactSummary]:
        artifacts = filter_items(
            self._artifacts.get(user_id, {}).values(),
            (project_id is not None, attr_equals("project_id", project_id)),
            (language, lambda a: a.language == language.lower()),
            (tags, shares_any("tags", tags or [])),
        )
        return [CodeArtifactSummary.model_validate(a) for a in newest_first(artifacts)]

    async def update_code_artifact(
        self, user_id: UUID, artifact_id: int, artifact_data: CodeArtifactUpdate,
    ) -> CodeArtifact:
        artifact = require_item(
            await self.get_code_artifact_by_id(user_id, artifact_id),
            f"Code artifact {artifact_id} not found",
        )

        update_data = artifact_data.model_dump(exclude_unset=True)
        if update_data.get("language"):
            update_data["language"] = update_data["language"].lower()
        return patch_item(artifact, update_data)

    async def delete_code_artifact(self, user_id: UUID, artifact_id: int) -> bool:
        return remove_item(self._artifacts, user_id, artifact_id)
