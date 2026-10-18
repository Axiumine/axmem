"""Shared MCP call helpers for the SQLite entity tool E2E test modules.

Every helper is a thin wrapper around ``execute_forgetful_tool`` that forwards the
given arguments unchanged, so the tests keep exercising the exact payloads they
describe. Assertions stay in the tests themselves.
"""
import json


async def call_tool(client, tool_name, arguments):
    """Invoke one registered tool through the execute_forgetful_tool meta-tool."""
    return await client.call_tool("execute_forgetful_tool", {
        "tool_name": tool_name, "arguments": arguments,
    })


async def create_entity(client, **fields):
    """Call create_entity with the given fields."""
    return await call_tool(client, "create_entity", fields)


async def get_entity(client, entity_id):
    """Fetch one entity by id."""
    return await call_tool(client, "get_entity", {"entity_id": entity_id})


async def list_entities(client, **filters):
    """Call list_entities with the given filters."""
    return await call_tool(client, "list_entities", filters)


async def update_entity(client, **fields):
    """Call update_entity with the given fields."""
    return await call_tool(client, "update_entity", fields)


async def create_project(client, **fields):
    """Call create_project with the given fields."""
    return await call_tool(client, "create_project", fields)


async def create_memory(client, **fields):
    """Call create_memory with the given fields."""
    return await call_tool(client, "create_memory", fields)


async def create_relationship(client, **fields):
    """Call create_entity_relationship with the given fields."""
    return await call_tool(client, "create_entity_relationship", fields)


async def get_entity_relationships(client, **fields):
    """Call get_entity_relationships with the given fields."""
    return await call_tool(client, "get_entity_relationships", fields)


async def link_entity_to_memory(client, entity_id, memory_id):
    """Link an entity to a memory."""
    return await call_tool(client, "link_entity_to_memory", {
        "entity_id": entity_id, "memory_id": memory_id,
    })


async def unlink_entity_from_memory(client, entity_id, memory_id):
    """Remove the link between an entity and a memory."""
    return await call_tool(client, "unlink_entity_from_memory", {
        "entity_id": entity_id, "memory_id": memory_id,
    })


async def link_entity_to_project(client, entity_id, project_id):
    """Link an entity to a project."""
    return await call_tool(client, "link_entity_to_project", {
        "entity_id": entity_id, "project_id": project_id,
    })


async def unlink_entity_from_project(client, entity_id, project_id):
    """Remove the link between an entity and a project."""
    return await call_tool(client, "unlink_entity_from_project", {
        "entity_id": entity_id, "project_id": project_id,
    })


async def get_entity_memories(client, entity_id):
    """Query the memories linked to an entity."""
    return await call_tool(client, "get_entity_memories", {"entity_id": entity_id})


async def get_memory_entities(client, memory_id):
    """Query the entities linked to a memory."""
    return await call_tool(client, "get_memory_entities", {"memory_id": memory_id})


async def search_entities(client, **query):
    """Call search_entities with the given query fields; returns the raw result."""
    return await call_tool(client, "search_entities", query)


def parse_text_payload(result):
    """Decode the JSON document carried in the first text content block."""
    return json.loads(result.content[0].text)
