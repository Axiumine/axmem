"""In-memory DocumentRepository stub."""

from datetime import UTC, datetime
from uuid import UUID

from app.models.document_models import (
    Document,
    DocumentCreate,
    DocumentSummary,
    DocumentUpdate,
)
from app.protocols.document_protocol import DocumentRepository
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


class InMemoryDocumentRepository(DocumentRepository):
    """In-memory implementation of DocumentRepository for testing"""

    def __init__(self):
        self._documents: dict[UUID, dict[int, Document]] = {}
        self._next_id = 1

    async def create_document(
        self, user_id: UUID, document_data: DocumentCreate,
    ) -> Document:
        document_id = self._next_id
        self._next_id += 1
        now = datetime.now(UTC)

        size_bytes = document_data.size_bytes or len(
            document_data.content.encode("utf-8"),
        )

        new_document = Document(
            id=document_id,
            title=document_data.title,
            description=document_data.description,
            content=document_data.content,
            document_type=document_data.document_type,
            filename=document_data.filename,
            size_bytes=size_bytes,
            tags=document_data.tags,
            project_id=None,
            created_at=now,
            updated_at=now,
            **provenance_kwargs(document_data),
        )
        return store_item(self._documents, user_id, document_id, new_document)

    async def get_document_by_id(
        self, user_id: UUID, document_id: int,
    ) -> Document | None:
        return find_item(self._documents, user_id, document_id)

    async def list_documents(
        self,
        user_id: UUID,
        project_id: int | None = None,
        document_type: str | None = None,
        tags: list[str] | None = None,
    ) -> list[DocumentSummary]:
        documents = filter_items(
            self._documents.get(user_id, {}).values(),
            (project_id is not None, attr_equals("project_id", project_id)),
            (document_type, attr_equals("document_type", document_type)),
            (tags, shares_any("tags", tags or [])),
        )
        return [DocumentSummary.model_validate(d) for d in newest_first(documents)]

    async def update_document(
        self, user_id: UUID, document_id: int, document_data: DocumentUpdate,
    ) -> Document:
        document = require_item(
            await self.get_document_by_id(user_id, document_id),
            f"Document {document_id} not found",
        )

        update_data = document_data.model_dump(exclude_unset=True)
        if update_data.get("content"):
            update_data["size_bytes"] = len(update_data["content"].encode("utf-8"))
        return patch_item(document, update_data)

    async def delete_document(self, user_id: UUID, document_id: int) -> bool:
        return remove_item(self._documents, user_id, document_id)
