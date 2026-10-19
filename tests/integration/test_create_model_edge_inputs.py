"""Request models: edge inputs for non-text values.

Covers memory text fields given as something other than a string.
"""
import pytest
from pydantic import ValidationError

from app.models.memory_models import MemoryCreate


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
