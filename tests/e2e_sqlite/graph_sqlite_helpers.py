"""Request builders and assertion helpers for the graph API SQLite E2E tests.

Shared by the ``test_api_graph*.py`` modules. The builders post the same request
bodies the tests used to spell out inline, and the readers pick nodes/edges out
of a response, so each test reads as a short scenario followed by its explicit
expectations.
"""
from contextlib import contextmanager

# Tiny 1x1 transparent PNG, base64-encoded — small file payload for tests
TINY_PNG_BASE64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYAAAAAYAAjCB0C8A"
    "AAAASUVORK5CYII="
)

# Relation segment of /skills/{id}/<relation> -> body field naming the target id
SKILL_LINK_FIELDS = {
    "files": "file_id",
    "code-artifacts": "code_artifact_id",
    "documents": "document_id",
}


@contextmanager
def auto_link_disabled():
    """Disable memory auto-linking so the graph structure is exactly what we link."""
    from app.config.settings import settings

    original_auto_link = settings.MEMORY_NUM_AUTO_LINK
    settings.MEMORY_NUM_AUTO_LINK = 0
    try:
        yield
    finally:
        settings.MEMORY_NUM_AUTO_LINK = original_auto_link


# --- Builders: create resources through the public REST API -----------------


async def create_resource(http, path, payload):
    """POST ``payload`` to ``path``, assert it was created and return the new id."""
    response = await http.post(path, json=payload)
    assert response.status_code in (200, 201), response.text
    return response.json()["id"]


async def create_memory(
    http, title, content, context, keywords, tags, importance=7, **extra,
):
    """Create a memory (default importance 7) and return its id."""
    payload = {
        "title": title,
        "content": content,
        "context": context,
        "keywords": keywords,
        "tags": tags,
        "importance": importance,
        **extra,
    }
    return await create_resource(http, "/api/v1/memories", payload)


async def create_memory_series(http, count, title, content, context, keyword, tag):
    """Create ``count`` memories; title/content/keyword templates take ``{i}``."""
    return [
        await create_memory(
            http,
            title.format(i=i),
            content.format(i=i),
            context,
            [keyword.format(i=i)],
            [tag],
        )
        for i in range(count)
    ]


async def create_entity(http, name, entity_type, **extra):
    """Create an entity and return its id."""
    payload = {"name": name, "entity_type": entity_type, **extra}
    return await create_resource(http, "/api/v1/entities", payload)


async def create_project(http, name, description):
    """Create a development project and return its id."""
    payload = {
        "name": name,
        "description": description,
        "project_type": "development",
    }
    return await create_resource(http, "/api/v1/projects", payload)


async def create_document(http, title, description, content, **extra):
    """Create a text document and return its id."""
    payload = {
        "title": title,
        "description": description,
        "content": content,
        "document_type": "text",
        **extra,
    }
    return await create_resource(http, "/api/v1/documents", payload)


async def create_code_artifact(http, title, description, code, **extra):
    """Create a python code artifact and return its id."""
    payload = {
        "title": title,
        "description": description,
        "code": code,
        "language": "python",
        **extra,
    }
    return await create_resource(http, "/api/v1/code-artifacts", payload)


async def create_file(http, filename, description, **extra):
    """Create a tiny PNG file and return its id."""
    payload = {
        "filename": filename,
        "description": description,
        "data": TINY_PNG_BASE64,
        "mime_type": "image/png",
        **extra,
    }
    return await create_resource(http, "/api/v1/files", payload)


async def create_skill(http, name, description, content, **extra):
    """Create a skill (tagged ``test``, importance 7) and return its id."""
    payload = {
        "name": name,
        "description": description,
        "content": content,
        "tags": ["test"],
        "importance": 7,
        **extra,
    }
    return await create_resource(http, "/api/v1/skills", payload)


async def make_project_plan_tasks(
    http_client, project_name="P", task_titles=("Task A",),
):
    """Create a project + plan + tasks chain via the public API."""
    project_resp = await http_client.post("/api/v1/projects", json={
        "name": project_name,
        "description": "P for plan/task graph test",
        "project_type": "development",
    })
    assert project_resp.status_code in (200, 201)
    project_id = project_resp.json()["id"]

    plan_resp = await http_client.post("/api/v1/plans", json={
        "title": f"{project_name} Plan",
        "project_id": project_id,
        "goal": "Goal",
    })
    assert plan_resp.status_code in (200, 201), plan_resp.text
    plan_id = plan_resp.json()["id"]

    task_ids = []
    for title in task_titles:
        task_resp = await http_client.post("/api/v1/tasks", json={
            "title": title, "plan_id": plan_id,
        })
        assert task_resp.status_code in (200, 201), task_resp.text
        task_ids.append(task_resp.json()["id"])
    return project_id, plan_id, task_ids


# --- Builders: link existing resources ---------------------------------------


async def link_memories(http, memory_id, related_id):
    """Link ``memory_id`` to ``related_id``."""
    await http.post(
        f"/api/v1/memories/{memory_id}/links", json={"related_ids": [related_id]},
    )


async def link_entity_memory(http, entity_id, memory_id):
    """Link an entity to a memory."""
    await http.post(
        f"/api/v1/entities/{entity_id}/memories", json={"memory_id": memory_id},
    )


async def relate_entities(http, source_id, target_id, relationship_type, **extra):
    """Create a ``relationship_type`` relationship from one entity to another."""
    await http.post(f"/api/v1/entities/{source_id}/relationships", json={
        "target_entity_id": target_id,
        "relationship_type": relationship_type,
        **extra,
    })


async def link_skill(http, skill_id, relation, target_id):
    """Link a skill to a file, code artifact or document; return the response."""
    return await http.post(
        f"/api/v1/skills/{skill_id}/{relation}",
        json={SKILL_LINK_FIELDS[relation]: target_id},
    )


# --- Readers over graph / subgraph response bodies ---------------------------


def nodes_of_type(data, node_type):
    """Return the graph nodes of ``node_type``."""
    return [n for n in data["nodes"] if n["type"] == node_type]


def edges_of_type(data, edge_type, **endpoints):
    """Return edges of ``edge_type``, optionally filtered by source/target."""
    return [
        e for e in data["edges"]
        if e["type"] == edge_type
        and all(e[key] == value for key, value in endpoints.items())
    ]


def node_ids(data):
    """Return the set of node ids in the response."""
    return {n["id"] for n in data["nodes"]}


def find_node(data, node_id):
    """Return the node with ``node_id`` (raises StopIteration when absent)."""
    return next(n for n in data["nodes"] if n["id"] == node_id)


# --- Assertions ---------------------------------------------------------------


def assert_ok(response):
    """Assert HTTP 200 and return the JSON body."""
    assert response.status_code == 200
    return response.json()
