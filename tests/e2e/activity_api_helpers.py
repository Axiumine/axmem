"""Shared request and stream helpers for the PostgreSQL activity API E2E tests.

Used by the ``test_api_activity*`` modules in this directory. The helpers only build and
send the requests the tests used to inline; every assertion stays in the tests themselves.
"""
import asyncio
import contextlib
import json


async def create_memory(http_client, title, content, context, keywords, importance=7):
    """POST a memory with the standard test tag and return the raw response."""
    return await http_client.post("/api/v1/memories", json={
        "title": title,
        "content": content,
        "context": context,
        "keywords": keywords,
        "tags": ["test"],
        "importance": importance,
    })


async def create_project(http_client, name, description):
    """POST a development project and return the raw response."""
    return await http_client.post("/api/v1/projects", json={
        "name": name,
        "description": description,
        "project_type": "development",
    })


async def create_document(http_client, title, description, content, tags):
    """POST a text document and return the raw response."""
    return await http_client.post("/api/v1/documents", json={
        "title": title,
        "description": description,
        "content": content,
        "document_type": "text",
        "tags": tags,
    })


async def create_code_artifact(http_client, title, description, code, tags):
    """POST a python code artifact and return the raw response."""
    return await http_client.post("/api/v1/code-artifacts", json={
        "title": title,
        "description": description,
        "code": code,
        "language": "python",
        "tags": tags,
    })


async def create_entity(http_client, name, entity_type="Individual", **extra):
    """POST an entity (test tag included) and return the raw response."""
    return await http_client.post("/api/v1/entities", json={
        "name": name,
        "entity_type": entity_type,
        **extra,
        "tags": ["test"],
    })


async def create_relationship(http_client, source_id, target_id):
    """POST a works_for relationship from ``source_id`` to ``target_id``."""
    return await http_client.post(
        f"/api/v1/entities/{source_id}/relationships",
        json={"target_entity_id": target_id, "relationship_type": "works_for"},
    )


async def collect_sse_events(http_client, url, received, timeout, stop_after=None):
    """Append decoded ``data:`` payloads from an SSE stream to ``received``.

    Stops once ``stop_after`` events arrived (never, when None). Stream errors are
    printed rather than raised so the surrounding test reports the empty result.
    """
    try:
        async with http_client.stream("GET", url, timeout=timeout) as response:
            async for line in response.aiter_lines():
                if line.startswith("data:"):
                    received.append(json.loads(line[5:].strip()))
                    if stop_after is not None and len(received) >= stop_after:
                        return
    except Exception as e:
        print(f"Stream error: {e}")


async def wait_or_cancel(task, timeout):
    """Wait for ``task`` up to ``timeout`` seconds, cancelling it on timeout."""
    try:
        await asyncio.wait_for(task, timeout=timeout)
    except TimeoutError:
        task.cancel()


async def cancel_and_drain(task):
    """Cancel ``task`` and swallow the resulting cancellation."""
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task
