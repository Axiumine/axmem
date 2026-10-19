"""PATCH-style update models: cleaning of list and text inputs.

Covers how every update model cleans padded or blank source file paths, how an empty list
clears tags and alternative names, which blank names are refused, and that an update that
was already built validates to itself. Complements the null-handling tests in
test_null_update_resources.py.
"""

import pytest
from pydantic import ValidationError

from app.models.code_artifact_models import CodeArtifactUpdate
from app.models.document_models import DocumentUpdate
from app.models.entity_models import (
    EntityRelationshipCreate,
    EntityRelationshipUpdate,
    EntityType,
    EntityUpdate,
)
from app.models.memory_models import MemoryUpdate
from app.models.project_models import ProjectUpdate

UPDATE_MODELS = [
    CodeArtifactUpdate,
    DocumentUpdate,
    EntityUpdate,
    EntityRelationshipUpdate,
    MemoryUpdate,
    ProjectUpdate,
]


@pytest.mark.parametrize("model", UPDATE_MODELS, ids=lambda m: m.__name__)
class TestSourceFilesOnUpdate:
    def test_paths_are_trimmed_and_blank_entries_dropped(self, model):
        update = model(source_files=["  src/app.py ", "", "   ", "docs/readme.md"])

        assert update.source_files == ["src/app.py", "docs/readme.md"]

    def test_null_means_unchanged(self, model):
        update = model(source_files=None)

        assert update.source_files is None
        assert "source_files" not in update.model_fields_set


@pytest.mark.parametrize(
    ("model", "field"),
    [
        (CodeArtifactUpdate, "tags"),
        (DocumentUpdate, "tags"),
        (EntityUpdate, "tags"),
        (EntityUpdate, "aka"),
    ],
    ids=lambda value: value if isinstance(value, str) else value.__name__,
)
def test_empty_list_clears_the_field_instead_of_leaving_it_unchanged(model, field):
    update = model(**{field: []})

    assert getattr(update, field) == []
    assert field in update.model_fields_set


@pytest.mark.parametrize("model", UPDATE_MODELS, ids=lambda m: m.__name__)
@pytest.mark.parametrize("payload", ["text", 5, ["title"], None], ids=["text", "number", "list", "null"])
def test_a_payload_that_is_not_an_object_is_rejected_as_a_model_type_error(model, payload):
    with pytest.raises(ValidationError) as excinfo:
        model.model_validate(payload)

    error = excinfo.value.errors()[0]
    assert error["type"] == "model_type"
    assert error["msg"] == f"Input should be a valid dictionary or instance of {model.__name__}"


class TestEntityUpdateCustomType:
    def test_other_without_custom_type_is_rejected(self):
        with pytest.raises(ValidationError, match="custom_type is required when entity_type is 'Other'"):
            EntityUpdate(entity_type=EntityType.OTHER)

    def test_other_with_custom_type_is_accepted(self):
        update = EntityUpdate(entity_type=EntityType.OTHER, custom_type="Middleware")

        assert update.entity_type == EntityType.OTHER
        assert update.custom_type == "Middleware"


class TestBlankRelationshipType:
    @pytest.mark.parametrize("blank", ["   ", " \t\n "])
    def test_create_rejects_a_blank_type(self, blank):
        with pytest.raises(ValidationError, match="relationship_type cannot be empty or whitespace only"):
            EntityRelationshipCreate(source_entity_id=1, target_entity_id=2, relationship_type=blank)

    @pytest.mark.parametrize("blank", ["   ", " \t\n "])
    def test_update_rejects_a_blank_type(self, blank):
        with pytest.raises(ValidationError, match="relationship_type cannot be empty or whitespace only"):
            EntityRelationshipUpdate(relationship_type=blank)

    def test_padding_around_the_type_is_trimmed_on_create_and_update(self):
        created = EntityRelationshipCreate(source_entity_id=1, target_entity_id=2, relationship_type="  works_at ")
        updated = EntityRelationshipUpdate(relationship_type="  works_at ")

        assert created.relationship_type == "works_at"
        assert updated.relationship_type == "works_at"
