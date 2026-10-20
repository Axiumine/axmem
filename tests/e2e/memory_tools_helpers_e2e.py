"""Call helpers shared by the memory tool end-to-end tests.

Every helper wraps one ``execute_forgetful_tool`` round trip so that the test
modules read as scenarios instead of nested payload dictionaries.
"""
import json

META_TOOL = "execute_forgetful_tool"


async def call_tool(client, tool_name, **arguments):
    """Run one forgetful tool through the execute_forgetful_tool meta-tool."""
    return await client.call_tool(
        META_TOOL, {"tool_name": tool_name, "arguments": arguments},
    )


async def create_memory(
    client, title, content, context, keywords, tags, importance, **extra,
):
    """Create a memory; extra keyword arguments are passed through to the tool."""
    return await call_tool(
        client, "create_memory", title=title, content=content, context=context,
        keywords=keywords, tags=tags, importance=importance, **extra,
    )


async def get_memory(client, memory_id):
    """Fetch one memory by ID."""
    return await call_tool(client, "get_memory", memory_id=memory_id)


async def query_memory(client, query, query_context, k=10, **extra):
    """Run a semantic query without link expansion unless overridden."""
    extra.setdefault("include_links", False)
    return await call_tool(
        client, "query_memory", query=query, query_context=query_context, k=k,
        **extra,
    )


async def mark_obsolete(client, memory_id, reason, **extra):
    """Soft-delete a memory."""
    return await call_tool(
        client, "mark_memory_obsolete", memory_id=memory_id, reason=reason, **extra,
    )


def recent_envelope(result):
    """Decode the get_recent_memories JSON envelope from a tool result."""
    return json.loads(result.content[0].text)


def find_by_id(items, item_id):
    """Return the first item whose id matches, or None."""
    return next((item for item in items if item["id"] == item_id), None)


def primary_ids(query_result):
    """IDs of the primary memories in a query_memory result."""
    return [m["id"] for m in query_result.data["primary_memories"]]


async def update_memory(client, memory_id, **fields):
    """Patch the given fields of a memory."""
    return await call_tool(client, "update_memory", memory_id=memory_id, **fields)


async def recent_memories(client, **arguments):
    """Run get_recent_memories with the given filter arguments."""
    return await call_tool(client, "get_recent_memories", **arguments)


async def create_numbered_memories(
    client, count, title, content, context, keywords, tag, importance=7,
):
    """Create ``count`` memories; title and content are templates taking ``{i}``."""
    for i in range(count):
        await create_memory(
            client, title.format(i=i), content.format(i=i), context, keywords,
            [tag], importance,
        )
