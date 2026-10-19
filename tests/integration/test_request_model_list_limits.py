"""Request models: the configured maximum number of tags and alternative names.

The cap is enforced by the field itself, ahead of the cleaning validators, on create and
on update. Blank and padded entries are dropped only after the cap was checked.
"""
import pytest
from pydantic import ValidationError

from app.config.settings import settings
from app.models.code_artifact_models import CodeArtifactCreate, CodeArtifactUpdate
from app.models.document_models import DocumentCreate, DocumentUpdate
from app.models.entity_models import EntityCreate, EntityType, EntityUpdate

_BASE_FIELDS = {
    CodeArtifactCreate: {"title": "t", "description": "d", "code": "x = 1", "language": "python"},
    DocumentCreate: {"title": "t", "description": "d", "content": "c"},
    EntityCreate: {"name": "n", "entity_type": EntityType.TEAM},
    CodeArtifactUpdate: {},
    DocumentUpdate: {},
    EntityUpdate: {},
}
LIMITED_LISTS = [
    (CodeArtifactCreate, "tags", settings.CODE_ARTIFACT_TAGS_MAX_COUNT),
    (CodeArtifactUpdate, "tags", settings.CODE_ARTIFACT_TAGS_MAX_COUNT),
    (DocumentCreate, "tags", settings.DOCUMENT_TAGS_MAX_COUNT),
    (DocumentUpdate, "tags", settings.DOCUMENT_TAGS_MAX_COUNT),
    (EntityCreate, "tags", settings.ENTITY_TAGS_MAX_COUNT),
    (EntityUpdate, "tags", settings.ENTITY_TAGS_MAX_COUNT),
    (EntityCreate, "aka", settings.ENTITY_AKA_MAX_COUNT),
    (EntityUpdate, "aka", settings.ENTITY_AKA_MAX_COUNT),
]
IDS = [f"{model.__name__}-{field}" for model, field, _ in LIMITED_LISTS]


def _build(model, field, values):
    return model(**{**_BASE_FIELDS[model], field: values})


@pytest.mark.parametrize(("model", "field", "limit"), LIMITED_LISTS, ids=IDS)
class TestListCap:
    def test_one_more_entry_than_the_cap_is_rejected(self, model, field, limit):
        with pytest.raises(ValidationError) as excinfo:
            _build(model, field, [f"item-{i}" for i in range(limit + 1)])

        error = excinfo.value.errors()[0]
        assert error["loc"] == (field,)
        assert error["type"] == "too_long"
        assert error["msg"] == f"List should have at most {limit} items after validation, not {limit + 1}"

    def test_exactly_the_cap_is_accepted(self, model, field, limit):
        values = [f"item-{i}" for i in range(limit)]

        assert getattr(_build(model, field, values), field) == values

    def test_blank_entries_count_towards_the_cap_before_they_are_dropped(self, model, field, limit):
        values = [f"item-{i}" for i in range(limit)] + ["   "]

        with pytest.raises(ValidationError) as excinfo:
            _build(model, field, values)

        assert excinfo.value.errors()[0]["type"] == "too_long"

    def test_padding_is_trimmed_and_blank_entries_are_dropped_below_the_cap(self, model, field, limit):
        cleaned = _build(model, field, ["  first ", "", "   ", "second"])

        assert getattr(cleaned, field) == ["first", "second"]
