"""Request models: edge inputs for enum spelling and non-text values.

Covers entity types given as something other than text, and memory text fields given as
something other than a string.
"""
import pytest
from pydantic import ValidationError

from app.models.entity_models import EntityCreate, EntityType
from app.models.memory_models import MemoryCreate


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
