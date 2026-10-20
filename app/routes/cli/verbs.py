"""Curated CLI verbs: friendly flags mapped onto registry tool arguments.

Every verb goes through the ToolExecutor seam, so it works identically against the
in-process runtime and a remote deployment by construction. Payloads are normalized
with to_jsonable first because local execution returns pydantic models while remote
execution returns plain dicts.
"""
from typing import Any

from app.protocols.executor import ToolExecutor
from app.routes.cli.render import (
    render_memory_detail,
    render_memory_lines,
    sanitize_line,
    to_jsonable,
)

DEFAULT_SAVE_CONTEXT = "Saved from the Forgetful CLI"


async def run(executor: ToolExecutor, args) -> tuple[Any, str]:
    """Execute a curated verb; returns (json payload, human rendering)."""
    handler = {
        "search": _memory_search,
        "save": _memory_save,
        "get": _memory_get,
        "recent": _memory_recent,
    }[args.memory_command]
    return await handler(executor, args)


async def _memory_search(executor: ToolExecutor, args) -> tuple[Any, str]:
    arguments: dict[str, Any] = {
        "query": args.query,
        "query_context": args.context,
    }
    if args.limit is not None:
        arguments["k"] = args.limit
    if args.project is not None:
        arguments["project"] = args.project

    payload = to_jsonable(await executor.execute("query_memory", arguments))
    return payload, render_memory_lines(payload.get("primary_memories", []))


async def _memory_save(executor: ToolExecutor, args) -> tuple[Any, str]:
    arguments: dict[str, Any] = {
        "title": args.title,
        "content": args.content,
        "context": DEFAULT_SAVE_CONTEXT,
        "keywords": [],
        "tags": [],
        "importance": args.importance,
    }
    if args.project is not None:
        arguments["project"] = args.project

    payload = to_jsonable(await executor.execute("create_memory", arguments))
    return payload, f"Created memory {payload['id']}: {sanitize_line(payload['title'])}"


async def _memory_get(executor: ToolExecutor, args) -> tuple[Any, str]:
    payload = to_jsonable(await executor.execute("get_memory", {"memory_id": args.memory_id}))
    return payload, render_memory_detail(payload)


async def _memory_recent(executor: ToolExecutor, args) -> tuple[Any, str]:
    arguments: dict[str, Any] = {"limit": args.limit}
    if args.project is not None:
        arguments["project"] = args.project

    payload = to_jsonable(await executor.execute("get_recent_memories", arguments))
    return payload, render_memory_lines(payload.get("memories", []))
