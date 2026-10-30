# SPDX-FileCopyrightText: 2026 Giovanni Manzoni
# SPDX-License-Identifier: AGPL-3.0-only

"""Integration tests for the two views of a tool that the meta-tools return (app/models/tool_registry_models.py)

``discover_tools`` returns the light view of each tool and ``how_to_use`` the full one. Their keys and values are
what an agent reads to call a tool, so both views are pinned whole. The JSON schema inside the full view is covered
by ``test_tool_metadata_json_schema.py``.
"""
from app.models.tool_registry_models import (
    ToolCategory,
    ToolImplementation,
    ToolMetadata,
    ToolParameter,
)


def _store() -> ToolMetadata:
    return ToolMetadata(
        name="store_note",
        category=ToolCategory.LINKING,
        description="Stores a note",
        parameters=[
            ToolParameter(
                name="text", type="str", description="What to store", required=True, example="remember this",
            ),
            ToolParameter(
                name="limit", type="Optional[int]", description="How many", required=False, default=5, example=10,
            ),
            ToolParameter(name="ctx", type="Context", description="Request context", required=True),
        ],
        returns="The stored note",
        examples=["store_note('a')", "store_note('b', 2)"],
        tags=["notes", "write"],
        mutates=True,
    )


def _plain() -> ToolMetadata:
    return ToolMetadata(
        name="list_notes",
        category=ToolCategory.MEMORY,
        description="Lists notes",
        parameters=[ToolParameter(name="query", type="str", description="What to look for")],
        returns="The notes",
    )


def test_the_light_view_names_the_tool_its_parameters_and_its_first_example():
    assert _store().to_discovery_dict() == {
        "name": "store_note",
        "category": "linking",
        "description": "Stores a note",
        "mutates": True,
        "parameters": {
            "text": {"type": "str", "required": True, "description": "What to store", "example": "remember this"},
            "limit": {"type": "Optional[int]", "required": False, "description": "How many", "example": 10},
            "ctx": {"type": "Context", "required": True, "description": "Request context", "example": None},
        },
        "returns": "The stored note",
        "example": "store_note('a')",
    }


def test_the_light_view_of_a_tool_without_examples_has_none_and_does_not_mutate():
    assert _plain().to_discovery_dict() == {
        "name": "list_notes",
        "category": "memory",
        "description": "Lists notes",
        "mutates": False,
        "parameters": {"query": {"type": "str", "required": False, "description": "What to look for", "example": None}},
        "returns": "The notes",
        "example": None,
    }


def test_the_full_view_lists_every_parameter_in_order_with_its_default_and_the_schema_of_the_ones_to_pass():
    assert _store().to_detailed_dict() == {
        "name": "store_note",
        "category": "linking",
        "description": "Stores a note",
        "mutates": True,
        "parameters": [
            {
                "name": "text", "type": "str", "description": "What to store", "required": True, "default": None,
                "example": "remember this",
            },
            {
                "name": "limit", "type": "Optional[int]", "description": "How many", "required": False, "default": 5,
                "example": 10,
            },
            {
                "name": "ctx", "type": "Context", "description": "Request context", "required": True, "default": None,
                "example": None,
            },
        ],
        "returns": "The stored note",
        "examples": ["store_note('a')", "store_note('b', 2)"],
        "tags": ["notes", "write"],
        "json_schema": {
            "type": "object",
            "properties": {
                "text": {"description": "What to store", "type": "string", "example": "remember this"},
                "limit": {"description": "How many", "type": "integer", "example": 10},
            },
            "required": ["text"],
        },
    }


def test_the_full_view_of_a_tool_without_examples_or_tags_has_empty_lists():
    detailed = _plain().to_detailed_dict()

    assert (detailed["examples"], detailed["tags"], detailed["mutates"]) == ([], [], False)
    assert detailed["json_schema"] == {
        "type": "object",
        "properties": {"query": {"description": "What to look for", "type": "string"}},
    }


def test_a_parameter_is_optional_and_has_no_default_or_example_unless_told_otherwise():
    parameter = ToolParameter(name="flag", type="bool", description="A switch")

    assert (parameter.required, parameter.default, parameter.example) == (False, None, None)


def test_a_tool_does_not_mutate_and_has_no_examples_or_tags_unless_told_otherwise():
    tool = _plain()

    assert (tool.mutates, tool.examples, tool.tags) == (False, [], [])
    assert tool.examples is not _store().examples


def test_the_two_categories_are_memory_and_linking():
    assert [(category.name, category.value) for category in ToolCategory] == [("MEMORY", "memory"), ("LINKING", "linking")]


async def test_an_implementation_carries_its_metadata_and_the_function_to_call():
    async def implementation(text: str) -> str:
        return text.upper()

    tool = ToolImplementation(metadata=_plain(), implementation=implementation)

    assert tool.metadata.name == "list_notes"
    assert await tool.implementation("a") == "A"
