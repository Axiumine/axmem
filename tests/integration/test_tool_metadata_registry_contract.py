# SPDX-FileCopyrightText: 2026 Giovanni Manzoni
# SPDX-License-Identifier: AGPL-3.0-only

"""The tool catalogue is what a client reads: names, descriptions, parameters, defaults, examples, tags, the write flag.

``discover_tools`` and ``how_to_use`` answer with the metadata that ``tool_metadata_registry`` registers, and the scope
resolver grants a ``read`` token the tools whose ``mutates`` flag is off. A text in the catalogue that changes by accident
(a parameter that loses its default, a description that gains a stray word, a write tool that reads as a read tool) is a
change of the contract of the server, so every tool as it is handed to the registry is pinned as a snapshot, and the rules
that hold for every tool of it are asserted on their own.

A changed snapshot is a contract change: review the diff, then refresh deliberately with
``uv run pytest -m snapshot --snapshot-update``.
"""
import copy
import json
import logging
from types import SimpleNamespace
from uuid import UUID

import pytest
from syrupy.extensions.json import JSONSnapshotExtension

from app.models.tool_registry_models import ToolCategory, ToolParameter
from app.routes.mcp import tool_metadata_registry
from app.routes.mcp.tool_adapters import create_memory_adapters
from app.routes.mcp.tool_metadata_registry import (
    register_all_tools_metadata,
    register_memory_tools_metadata,
    register_simplified_tool,
)
from app.routes.mcp.tool_registry import ToolRegistry

LOGGER = "app.routes.mcp.tool_metadata_registry"
MEMORY_ID = UUID("018f2c3e-0000-7000-8000-000000000042")
TOOL_NAMES = [
    "create_memory",
    "query_memory",
    "update_memory",
    "link_memories",
    "unlink_memories",
    "get_memory",
    "mark_memory_obsolete",
    "unobsolete_memory",
    "get_recent_memories",
    "rebuild_embeddings",
]
WRITE_TOOLS = {
    "create_memory",
    "update_memory",
    "link_memories",
    "unlink_memories",
    "mark_memory_obsolete",
    "unobsolete_memory",
    "rebuild_embeddings",
}


@pytest.fixture
def json_snapshot(snapshot):
    return snapshot.use_extension(JSONSnapshotExtension)


@pytest.fixture
def registered(monkeypatch):
    """The memory tools registered over the real adapters (no tool runs here, so no service is needed).

    ``calls`` holds what ``register_memory_tools_metadata`` handed to ``register_simplified_tool`` for each tool, which is
    the table of the tools as it is written: a key that the table leaves out reads as its default after the registration.
    """
    adapters = create_memory_adapters(SimpleNamespace(), SimpleNamespace())
    registry = ToolRegistry()
    calls = []

    def record(**kwargs):
        calls.append(copy.deepcopy({key: value for key, value in kwargs.items() if key not in {"registry", "implementation"}}))
        register_simplified_tool(**kwargs)

    monkeypatch.setattr(tool_metadata_registry, "register_simplified_tool", record)
    register_memory_tools_metadata(registry, adapters)
    return SimpleNamespace(registry=registry, adapters=adapters, calls=calls)


async def _implementation(**_arguments):
    return {}


@pytest.mark.snapshot
def test_table_of_the_memory_tools(registered, json_snapshot):
    assert [{**call, "category": str(call["category"])} for call in registered.calls] == json_snapshot


def test_the_tools_are_listed_in_the_order_a_client_reads_them(registered):
    assert [tool.name for tool in registered.registry.list_all_tools()] == TOOL_NAMES
    assert [call["name"] for call in registered.calls] == TOOL_NAMES
    assert registered.registry.list_categories() == {"memory": 10}


def test_every_tool_runs_the_adapter_of_its_own_name_and_every_adapter_has_a_tool(registered):
    assert set(registered.adapters) == set(TOOL_NAMES)
    for name in TOOL_NAMES:
        assert registered.registry.get_tool(name).implementation is registered.adapters[name]


def test_only_the_tools_that_write_are_marked_as_mutating(registered):
    tools = registered.registry.list_all_tools()

    assert {tool.name for tool in tools if tool.mutates} == WRITE_TOOLS
    assert {tool.name for tool in tools if not tool.mutates} == {"query_memory", "get_memory", "get_recent_memories"}


def test_every_example_is_a_call_of_the_tool_that_it_documents(registered):
    for tool in registered.registry.list_all_tools():
        assert tool.examples
        for example in tool.examples:
            call = f'execute_axmem_tool("{tool.name}", '
            assert example.startswith(call)
            assert isinstance(json.loads(example.removeprefix(call).removesuffix(")")), dict)


def test_a_parameter_that_gives_only_a_name_and_a_type_is_optional_without_a_default_or_an_example():
    registry = ToolRegistry()

    register_simplified_tool(
        registry=registry,
        name="probe",
        category=ToolCategory.MEMORY,
        description="A probe",
        parameters=[{"name": "bare", "type": "str"}],
        returns="Nothing",
        implementation=_implementation,
    )

    probe = registry.get_tool("probe")
    assert probe.metadata.parameters == [
        ToolParameter(name="bare", type="str", description="", required=False, default=None, example=None),
    ]
    assert probe.metadata.examples == []
    assert probe.metadata.tags == []
    assert probe.metadata.mutates is False
    assert probe.implementation is _implementation


def test_a_tool_keeps_every_field_that_it_is_registered_with():
    registry = ToolRegistry()
    parameters = [
        {"name": "limit", "type": "int", "description": "How many", "required": True, "default": 7, "example": 9},
        {"name": "label", "type": "Optional[str]", "description": "Which one", "required": False, "default": "x", "example": "y"},
    ]

    register_simplified_tool(
        registry,
        "probe",
        ToolCategory.LINKING,
        "A probe",
        parameters,
        "Some result",
        _implementation,
        examples=["probe()", "probe(limit=1)"],
        tags=["alpha", "beta"],
        mutates=True,
    )

    probe = registry.get_tool("probe")
    assert probe.metadata.model_dump() == {
        "name": "probe",
        "category": ToolCategory.LINKING,
        "description": "A probe",
        "parameters": [
            {"name": "limit", "type": "int", "description": "How many", "required": True, "default": 7, "example": 9},
            {"name": "label", "type": "Optional[str]", "description": "Which one", "required": False, "default": "x", "example": "y"},
        ],
        "returns": "Some result",
        "examples": ["probe()", "probe(limit=1)"],
        "tags": ["alpha", "beta"],
        "mutates": True,
    }
    assert probe.implementation is _implementation


def test_registering_the_memory_tools_logs_how_many_were_registered(caplog):
    caplog.set_level(logging.INFO, logger=LOGGER)

    register_memory_tools_metadata(ToolRegistry(), create_memory_adapters(SimpleNamespace(), SimpleNamespace()))

    assert [record.getMessage() for record in caplog.records if record.name == LOGGER] == ["Registered 10 memory tools"]


class _RecordingMemoryService:
    async def get_memory(self, user_id, memory_id):
        return SimpleNamespace(id=memory_id, served_by="memory service")


class _RecordingReEmbeddingService:
    async def validate_targeted_scope(self, user_id, memory_ids, project):
        self.scope = (memory_ids, project)

    async def rebuild_targeted(self, user_id, memory_ids, project):
        return SimpleNamespace(rebuilt_ids=["rebuilt"], skipped_ids=[], failed=[])


async def test_registering_everything_wires_each_service_into_the_tools_and_logs_the_summary(caplog):
    caplog.set_level(logging.INFO, logger=LOGGER)
    registry = ToolRegistry()
    re_embedding_service = _RecordingReEmbeddingService()
    context = SimpleNamespace(fastmcp=SimpleNamespace(auth=None, trusted_local=True))

    register_all_tools_metadata(registry, _RecordingMemoryService(), re_embedding_service)

    fetched = await registry.execute("get_memory", {"memory_id": str(MEMORY_ID)}, ctx=context)
    rebuilt = await registry.execute("rebuild_embeddings", {"project": "axmem"}, ctx=context)
    assert (fetched.id, fetched.served_by) == (MEMORY_ID, "memory service")
    assert rebuilt == {"rebuilt_ids": ["rebuilt"], "skipped_ids": [], "failed": []}
    assert re_embedding_service.scope == (None, "axmem")
    assert [tool.name for tool in registry.list_all_tools()] == TOOL_NAMES
    assert [record.getMessage() for record in caplog.records if record.name == LOGGER] == [
        "Starting tool registration",
        "Registered 10 memory tools",
        "Tool registration complete: 10 tools across 1 categories",
        "Categories: {'memory': 10}",
    ]
