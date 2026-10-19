"""PATCH-style update models: cleaning of list and text inputs.

Covers how every update model cleans padded or blank source file paths, how an empty list
clears tags and alternative names, which blank names are refused, and that an update that
was already built validates to itself. Complements the null-handling tests in
test_null_update_resources.py.
"""
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from app.config.settings import settings
from app.models.code_artifact_models import CodeArtifactUpdate
from app.models.document_models import DocumentUpdate
from app.models.entity_models import (
    EntityRelationshipCreate,
    EntityRelationshipUpdate,
    EntityType,
    EntityUpdate,
)
from app.models.file_models import FileUpdate
from app.models.memory_models import MemoryUpdate
from app.models.project_models import ProjectUpdate
from app.models.skill_models import Skill, SkillCreate, SkillUpdate

UPDATE_MODELS = [
    CodeArtifactUpdate,
    DocumentUpdate,
    EntityUpdate,
    EntityRelationshipUpdate,
    FileUpdate,
    MemoryUpdate,
    ProjectUpdate,
    SkillUpdate,
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
        (FileUpdate, "tags"),
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


class TestSkillNameAndTags:
    @pytest.mark.parametrize("blank", ["   ", " \t "])
    def test_create_rejects_a_blank_name(self, blank):
        with pytest.raises(ValidationError, match="name cannot be empty or whitespace only"):
            SkillCreate(name=blank, description="d", content="c")

    @pytest.mark.parametrize("blank", ["   ", " \t "])
    def test_update_rejects_a_blank_name(self, blank):
        with pytest.raises(ValidationError, match="name cannot be empty or whitespace only"):
            SkillUpdate(name=blank)

    @pytest.mark.parametrize("name", ["My Skill", "my_skill", "-leading", "trailing-", "double--hyphen", "UPPER"])
    def test_create_and_update_reject_names_that_are_not_kebab_case(self, name):
        with pytest.raises(ValidationError, match="name must be kebab-case"):
            SkillCreate(name=name, description="d", content="c")
        with pytest.raises(ValidationError, match="name must be kebab-case"):
            SkillUpdate(name=name)

    def test_update_trims_a_kebab_case_name(self):
        assert SkillUpdate(name="  my-skill ").name == "my-skill"

    def test_update_rejects_more_tags_than_the_configured_maximum(self):
        too_many = [f"tag-{i}" for i in range(settings.SKILL_TAGS_MAX_COUNT + 1)]

        with pytest.raises(ValidationError, match=f"Maximum {settings.SKILL_TAGS_MAX_COUNT} tags allowed"):
            SkillUpdate(tags=too_many)

    def test_update_accepts_exactly_the_configured_maximum_of_tags(self):
        tags = [f"tag-{i}" for i in range(settings.SKILL_TAGS_MAX_COUNT)]

        assert SkillUpdate(tags=tags).tags == tags

    def test_update_rejects_a_tag_that_is_too_long_to_index(self):
        with pytest.raises(ValidationError, match="Tags are limited to"):
            SkillUpdate(tags=["x" * (settings.SKILL_TAG_MAX_LENGTH + 1)])

    def test_response_model_still_reads_a_tag_that_is_too_long_to_index(self):
        now = datetime.now(tz=UTC)
        long_tag = "x" * (settings.SKILL_TAG_MAX_LENGTH + 1)

        skill = Skill(id=1, name="my-skill", description="d", content="c", tags=[long_tag], created_at=now, updated_at=now)

        assert skill.tags == [long_tag]
