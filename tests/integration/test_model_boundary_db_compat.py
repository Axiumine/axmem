"""Input models must reject values the database cannot store.

PostgreSQL text/varchar/jsonb columns cannot hold a NUL character, JSONB cannot hold
NaN/Infinity. SQLite accepts all of these and then
stores values that poison later reads (NaN in a JSON column makes every later read
fail). The request models are the one place both backends share, so that is where the
values are refused, with an error the REST routes can return as a 400.
"""
import json

import pytest
from pydantic import ValidationError

from app.models.entity_models import (
    EntityRelationship,
    EntityRelationshipCreate,
    EntityRelationshipUpdate,
)

NUL = "\x00"

REL = {"source_entity_id": 1, "target_entity_id": 2, "relationship_type": "knows"}
NOW = "2026-01-01T00:00:00Z"


def _rejected(model, payload):
    """Validate like the routes do; return the errors after proving they are JSON safe."""
    with pytest.raises(ValidationError) as caught:
        model(**payload)
    errors = caught.value.errors()
    # The REST routes return e.errors() as the 400 body: it must serialise, and must not
    # echo the offending value back (NaN cannot be written by a strict JSON encoder).
    json.dumps(errors, allow_nan=False)
    return errors


class TestRelationshipMetadata:
    @pytest.mark.parametrize("model,base", [
        (EntityRelationshipCreate, REL),
        (EntityRelationshipUpdate, {}),
    ])
    @pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf")])
    def test_non_finite_number_is_rejected(self, model, base, bad):
        errors = _rejected(model, {**base, "metadata": {"w": bad}})
        assert errors[0]["loc"] == ("metadata", "w")
        assert errors[0]["type"] == "non_finite_number"

    @pytest.mark.parametrize("token", ["NaN", "Infinity", "-Infinity", "1e999"])
    def test_non_finite_number_in_json_text_is_rejected(self, token):
        body = json.dumps({"metadata": {"deep": [1, {"w": "TOKEN"}]}}).replace('"TOKEN"', token)
        with pytest.raises(ValidationError) as caught:
            EntityRelationshipUpdate.model_validate_json(body)
        errors = caught.value.errors()
        assert errors[0]["loc"] == ("metadata", "deep", 1, "w")
        json.dumps(errors, allow_nan=False)

    @pytest.mark.parametrize("model,base", [
        (EntityRelationshipCreate, REL),
        (EntityRelationshipUpdate, {}),
    ])
    @pytest.mark.parametrize("metadata,loc", [
        ({"a": f"x{NUL}y"}, ("metadata", "a")),
        ({"a": [f"x{NUL}y"]}, ("metadata", "a", 0)),
        ({f"k{NUL}": "v"}, ("metadata",)),
    ])
    def test_nul_character_is_rejected(self, model, base, metadata, loc):
        errors = _rejected(model, {**base, "metadata": metadata})
        assert errors[0]["loc"] == loc
        assert errors[0]["type"] == "nul_character"

    def test_storable_metadata_is_unchanged(self):
        metadata = {"w": 1, "f": 0.5, "big": 1e300, "neg": -2.5, "none": None, "flag": True,
                    "text": "tab\tnewline\n emoji \U0001f600 é", "nested": {"l": [1, 2.5, "x"]}}
        assert EntityRelationshipCreate(**REL, metadata=metadata).metadata == metadata
        assert EntityRelationshipUpdate(metadata=metadata).metadata == metadata
        assert EntityRelationshipUpdate(metadata={}).metadata == {}

    def test_error_does_not_echo_the_value(self):
        errors = _rejected(EntityRelationshipCreate, {**REL, "metadata": {"a": "secret" + NUL}})
        assert "secret" not in json.dumps(errors)

    def test_deeply_nested_metadata_does_not_overflow_the_stack(self):
        value: object = float("nan")
        for _ in range(5000):
            value = [value]
        errors = _rejected(EntityRelationshipUpdate, {"metadata": {"w": value}})
        assert errors[0]["type"] == "non_finite_number"
        assert len(errors[0]["loc"]) == 5002

    def test_response_model_still_reads_rows_stored_earlier(self):
        row = {**REL, "id": 1, "created_at": NOW, "updated_at": NOW,
               "metadata": {"w": float("nan"), "a": f"x{NUL}"}}
        relationship = EntityRelationship(**row)
        assert relationship.metadata is not None
        assert relationship.metadata["a"] == f"x{NUL}"
