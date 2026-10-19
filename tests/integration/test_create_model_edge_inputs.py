"""Request models: edge inputs for sizes, enum spelling and non-text values.

Covers the decoded file size limit on create and update, the document size that is worked
out from the content when the caller passes a null, entity types given as something other
than text, and memory text fields given as something other than a string.
"""
import base64

import pytest
from pydantic import ValidationError

from app.config.settings import settings
from app.models.document_models import DocumentCreate
from app.models.entity_models import EntityCreate, EntityType
from app.models.file_models import FileCreate, FileUpdate
from app.models.memory_models import MemoryCreate


def _b64(payload: bytes) -> str:
    return base64.b64encode(payload).decode("ascii")


def _file_kwargs(payload: bytes) -> dict:
    return {
        "filename": "a.bin",
        "description": "d",
        "data": _b64(payload),
        "mime_type": "application/octet-stream",
    }


class TestDecodedFileSizeLimit:
    @pytest.fixture(autouse=True)
    def _small_limit(self, monkeypatch):
        monkeypatch.setattr(settings, "FILE_MAX_SIZE_BYTES", 8)

    def test_create_rejects_data_that_decodes_above_the_limit(self):
        with pytest.raises(ValidationError, match=r"Decoded file size \(9 bytes\) exceeds maximum \(8 bytes\)"):
            FileCreate(**_file_kwargs(b"123456789"))

    def test_update_rejects_data_that_decodes_above_the_limit(self):
        with pytest.raises(ValidationError, match=r"Decoded file size \(9 bytes\) exceeds maximum \(8 bytes\)"):
            FileUpdate(data=_b64(b"123456789"))

    def test_create_accepts_data_that_decodes_to_exactly_the_limit(self):
        file = FileCreate(**_file_kwargs(b"12345678"))

        assert base64.b64decode(file.data) == b"12345678"

    def test_update_accepts_data_that_decodes_to_exactly_the_limit(self):
        update = FileUpdate(data=_b64(b"12345678"))

        assert base64.b64decode(update.data) == b"12345678"


class TestDocumentSizeFromContent:
    def test_null_size_is_worked_out_from_the_utf8_length_of_the_content(self):
        document = DocumentCreate(title="t", description="d", content="héllo wörld", size_bytes=None)

        assert document.size_bytes == len("héllo wörld".encode())
        assert document.size_bytes == 13

    def test_explicit_size_is_kept(self):
        document = DocumentCreate(title="t", description="d", content="héllo", size_bytes=999)

        assert document.size_bytes == 999

    def test_null_size_with_empty_content_reports_only_the_content(self):
        with pytest.raises(ValidationError) as excinfo:
            DocumentCreate(title="t", description="d", content="", size_bytes=None)

        assert [error["loc"] for error in excinfo.value.errors()] == [("content",)]


class TestEntityTypeSpelling:
    @pytest.mark.parametrize("spelling", ["individual", "INDIVIDUAL", "iNdIvIdUaL"])
    def test_type_is_matched_without_regard_to_case(self, spelling):
        entity = EntityCreate(name="Sarah", entity_type=spelling)

        assert entity.entity_type is EntityType.INDIVIDUAL

    @pytest.mark.parametrize("value", [5, 1.5, ["Individual"], None])
    def test_a_value_that_is_not_text_is_rejected_with_the_allowed_types(self, value):
        with pytest.raises(ValidationError) as excinfo:
            EntityCreate(name="Sarah", entity_type=value)

        error = excinfo.value.errors()[0]
        assert error["loc"] == ("entity_type",)
        assert "'Organization', 'Individual', 'Team', 'Device', 'System' or 'Other'" in error["msg"]


class TestMemoryTextMustBeAString:
    @pytest.mark.parametrize("field", ["title", "content"])
    @pytest.mark.parametrize("value", [123, ["text"], {"text": "x"}])
    def test_other_types_are_rejected(self, field, value):
        data = {"title": "A title", "content": "Real content", field: value}

        with pytest.raises(ValidationError) as excinfo:
            MemoryCreate(**data)

        error = excinfo.value.errors()[0]
        assert error["loc"] == (field,)
        assert error["type"] == "string_type"
