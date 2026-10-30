# SPDX-FileCopyrightText: 2026 Giovanni Manzoni
# SPDX-License-Identifier: AGPL-3.0-only

"""JSON schema that ToolMetadata derives for a tool's parameters (shown by how_to_use)."""
import pytest

from app.models.tool_registry_models import ToolCategory, ToolMetadata, ToolParameter


def _tool(*parameters: ToolParameter) -> ToolMetadata:
    return ToolMetadata(
        name="sample_tool",
        category=ToolCategory.MEMORY,
        description="A sample tool",
        parameters=list(parameters),
        returns="Nothing",
    )


def _param(name: str, type_: str, **overrides) -> ToolParameter:
    return ToolParameter(name=name, type=type_, description=f"The {name}", **overrides)


def _schema(*parameters: ToolParameter) -> dict:
    return _tool(*parameters).to_detailed_dict()["json_schema"]


class TestParameterTypeMapping:
    @pytest.mark.parametrize(
        ("declared", "expected"),
        [
            ("str", "string"),
            ("int", "integer"),
            ("float", "number"),
            ("bool", "boolean"),
            ("dict", "object"),
            ("list", "array"),
            ("Dict[str, Any]", "object"),
            ("Dict[str, List[int]]", "object"),
            ("List[str]", "array"),
            ("List[Dict[str, Any]]", "array"),
            ("Optional[int]", "integer"),
            ("Optional[Dict[str, Any]]", "object"),
            ("Optional[List[str]]", "array"),
            ("STR", "string"),
            ("SomethingUnknown", "string"),
        ],
    )
    def test_declared_type_maps_to_its_json_schema_type(self, declared, expected):
        schema = _schema(_param("value", declared))

        assert schema["properties"]["value"]["type"] == expected


class TestSchemaShape:
    def test_required_parameters_are_listed_and_optional_ones_are_not(self):
        schema = _schema(_param("query", "str", required=True), _param("limit", "int"))

        assert schema == {
            "type": "object",
            "properties": {
                "query": {"description": "The query", "type": "string"},
                "limit": {"description": "The limit", "type": "integer"},
            },
            "required": ["query"],
        }

    def test_schema_has_no_required_list_when_every_parameter_is_optional(self):
        assert "required" not in _schema(_param("limit", "int"))

    def test_examples_are_included_only_when_given(self):
        schema = _schema(_param("limit", "int", example=5), _param("query", "str"))

        assert schema["properties"]["limit"]["example"] == 5
        assert "example" not in schema["properties"]["query"]

    def test_internal_ctx_parameter_is_left_out(self):
        schema = _schema(_param("ctx", "Context", required=True), _param("query", "str", required=True))

        assert list(schema["properties"]) == ["query"]
        assert schema["required"] == ["query"]
