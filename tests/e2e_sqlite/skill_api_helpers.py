"""Shared request helpers for the Skill REST API E2E tests (SQLite)."""

SKILLS_URL = "/api/v1/skills"

# Request body key used when linking each kind of resource to a skill
LINK_KEYS = {
    "files": "file_id",
    "code-artifacts": "code_artifact_id",
    "documents": "document_id",
}


async def post_skill(
    http_client, name, description, content, tags=None, importance=7, **extra,
):
    """POST a skill built from the given fields and return the raw response."""
    payload = {
        "name": name,
        "description": description,
        "content": content,
        "tags": tags if tags is not None else [],
        "importance": importance,
        **extra,
    }
    return await http_client.post(SKILLS_URL, json=payload)


async def post_skill_id(http_client, *args, **kwargs):
    """POST a skill and return the new skill ID."""
    response = await post_skill(http_client, *args, **kwargs)
    return response.json()["id"]


async def export_skill_md(http_client, skill_id):
    """Export a skill as SKILL.md and return the raw response."""
    return await http_client.get(f"{SKILLS_URL}/{skill_id}/export")


async def link_skill(http_client, skill_id, resource, resource_id):
    """Link a file, code artifact or document to a skill; return the response."""
    return await http_client.post(
        f"{SKILLS_URL}/{skill_id}/{resource}",
        json={LINK_KEYS[resource]: resource_id},
    )


async def unlink_skill(http_client, skill_id, resource, resource_id):
    """Unlink a file, code artifact or document from a skill."""
    return await http_client.delete(
        f"{SKILLS_URL}/{skill_id}/{resource}/{resource_id}",
    )
