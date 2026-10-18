"""In-memory FileRepository stub."""

import base64
from datetime import UTC, datetime
from uuid import UUID

from app.models.file_models import File, FileCreate, FileSummary, FileUpdate
from app.protocols.file_protocol import FileRepository
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


class InMemoryFileRepository(FileRepository):
    """In-memory implementation of FileRepository for testing"""

    def __init__(self):
        self._files: dict[UUID, dict[int, File]] = {}
        self._next_id = 1

    async def create_file(
        self, user_id: UUID, file_data: FileCreate,
    ) -> File:
        file_id = self._next_id
        self._next_id += 1
        now = datetime.now(UTC)

        size_bytes = len(base64.b64decode(file_data.data))

        new_file = File(
            id=file_id,
            filename=file_data.filename,
            description=file_data.description,
            data=file_data.data,
            mime_type=file_data.mime_type,
            size_bytes=size_bytes,
            tags=file_data.tags,
            project_id=file_data.project_id,
            created_at=now,
            updated_at=now,
            **provenance_kwargs(file_data),
        )
        return store_item(self._files, user_id, file_id, new_file)

    async def get_file_by_id(
        self, user_id: UUID, file_id: int,
    ) -> File | None:
        return find_item(self._files, user_id, file_id)

    async def list_files(
        self,
        user_id: UUID,
        project_id: int | None = None,
        mime_type: str | None = None,
        tags: list[str] | None = None,
    ) -> list[FileSummary]:
        files = filter_items(
            self._files.get(user_id, {}).values(),
            (project_id is not None, attr_equals("project_id", project_id)),
            (mime_type, attr_equals("mime_type", mime_type)),
            (tags, shares_any("tags", tags or [])),
        )
        return [FileSummary.model_validate(f) for f in newest_first(files)]

    async def update_file(
        self, user_id: UUID, file_id: int, file_data: FileUpdate,
    ) -> File:
        file_obj = require_item(
            await self.get_file_by_id(user_id, file_id),
            f"File {file_id} not found",
        )

        update_data = file_data.model_dump(exclude_unset=True)
        if update_data.get("data"):
            update_data["size_bytes"] = len(base64.b64decode(update_data["data"]))
        return patch_item(file_obj, update_data)

    async def delete_file(self, user_id: UUID, file_id: int) -> bool:
        return remove_item(self._files, user_id, file_id)
