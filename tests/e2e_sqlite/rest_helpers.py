"""Request builders for the REST SQLite E2E tests.

The builders post the same request bodies the tests used to spell out inline, so each
test reads as a short scenario followed by its explicit expectations.
"""
from contextlib import contextmanager


@contextmanager
def auto_link_disabled():
    """Disable memory auto-linking so the link structure is exactly what we link."""
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


async def create_entity(http, name, entity_type, **extra):
    """Create an entity and return its id."""
    payload = {"name": name, "entity_type": entity_type, **extra}
    return await create_resource(http, "/api/v1/entities", payload)


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
        "description": "P for plan/task test",
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
