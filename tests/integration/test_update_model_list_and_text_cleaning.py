# SPDX-FileCopyrightText: 2026 Giovanni Manzoni
# SPDX-License-Identifier: AGPL-3.0-only

"""PATCH-style update models: cleaning of list and text inputs.

Covers how every update model cleans padded or blank source file paths, and that an update
that was already built validates to itself.
"""

import pytest
from pydantic import ValidationError

from app.models.memory_models import MemoryUpdate

UPDATE_MODELS = [
    MemoryUpdate,
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


@pytest.mark.parametrize("model", UPDATE_MODELS, ids=lambda m: m.__name__)
@pytest.mark.parametrize("payload", ["text", 5, ["title"], None], ids=["text", "number", "list", "null"])
def test_a_payload_that_is_not_an_object_is_rejected_as_a_model_type_error(model, payload):
    with pytest.raises(ValidationError) as excinfo:
        model.model_validate(payload)

    error = excinfo.value.errors()[0]
    assert error["type"] == "model_type"
    assert error["msg"] == f"Input should be a valid dictionary or instance of {model.__name__}"
