"""Request and assertion helpers shared by the PostgreSQL graph E2E tests.

The helpers keep each test focused on the graph behaviour under test: fixtures
are created through the REST API (asserting that the create call succeeded) and
the repeated node, edge and meta checks live in small named assertions.
"""

# Tiny 1x1 transparent PNG, base64-encoded — small file payload for E2E tests
TINY_PNG_BASE64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYAAAAAYAAjCB0C8A"
    "AAAASUVORK5CYII="
)

_CREATED_STATUSES = (200, 201)


# ---- Fixture creation through the REST API ----

async def create_resource(http_client, path, payload):
    """POST a payload, assert a successful create and return the new resource id."""
    response = await http_client.post(path, json=payload)
    assert response.status_code in _CREATED_STATUSES, response.text
    return response.json()["id"]


async def link_resources(http_client, path, payload):
    """POST an association payload and assert the link was accepted."""
    response = await http_client.post(path, json=payload)
    assert response.status_code in _CREATED_STATUSES, response.text


async def create_project(http_client, name, description, **extra):
    """Create a development project and return its id."""
    return await create_resource(http_client, "/api/v1/projects", {
        "name": name,
        "description": description,
        "project_type": "development",
        **extra,
    })


async def create_document(http_client, title, description, content, **extra):
    """Create a text document and return its id."""
    return await create_resource(http_client, "/api/v1/documents", {
        "title": title,
        "description": description,
        "content": content,
        "document_type": "text",
        **extra,
    })


async def create_code_artifact(http_client, title, description, code, **extra):
    """Create a python code artifact and return its id."""
    return await create_resource(http_client, "/api/v1/code-artifacts", {
        "title": title,
        "description": description,
        "code": code,
        "language": "python",
        **extra,
    })


async def create_memory(
    http_client, title, content, context, keywords,
    *, tags=("e2e-test",), importance=7, **links,
):
    """Create a memory (``links`` carries project_ids, file_ids, ...) and return its id."""
    return await create_resource(http_client, "/api/v1/memories", {
        "title": title,
        "content": content,
        "context": context,
        "keywords": keywords,
        "tags": list(tags),
        "importance": importance,
        **links,
    })


async def create_entity(http_client, name, entity_type, notes):
    """Create an entity and return its id."""
    return await create_resource(http_client, "/api/v1/entities", {
        "name": name,
        "entity_type": entity_type,
        "notes": notes,
    })


async def create_file(http_client, filename, description, **extra):
    """Create a tiny PNG file resource and return its id."""
    return await create_resource(http_client, "/api/v1/files", {
        "filename": filename,
        "description": description,
        "data": TINY_PNG_BASE64,
        "mime_type": "image/png",
        **extra,
    })


async def create_skill(http_client, name, description, content, **extra):
    """Create a skill (tagged e2e-test, importance 7) and return its id."""
    return await create_resource(http_client, "/api/v1/skills", {
        "name": name,
        "description": description,
        "content": content,
        "tags": ["e2e-test"],
        "importance": 7,
        **extra,
    })


# ---- Graph payload accessors ----

def nodes_of_type(data, node_type):
    """Return the graph nodes whose ``type`` matches."""
    return [n for n in data["nodes"] if n["type"] == node_type]


def edges_of_type(data, edge_type):
    """Return the graph edges whose ``type`` matches."""
    return [e for e in data["edges"] if e["type"] == edge_type]


def graph_node_ids(data):
    """Return the set of graph node ids (for example ``memory_3``)."""
    return {n["id"] for n in data["nodes"]}


# ---- Graph payload assertions ----

def assert_node_types_present(data, *node_types):
    """Assert the graph holds at least one node of every given type."""
    missing = set(node_types) - {n["type"] for n in data["nodes"]}
    assert not missing, f"Missing node types: {sorted(missing)}"


def assert_edge_types_present(data, *edge_types):
    """Assert the graph holds at least one edge of every given type."""
    missing = set(edge_types) - {e["type"] for e in data["edges"]}
    assert not missing, f"Missing edge types: {sorted(missing)}"


def assert_typed_nodes_present(data, node_type, *entity_ids):
    """Assert nodes of the given type exist for every entity id (``data.id``)."""
    found = {n["data"]["id"] for n in nodes_of_type(data, node_type)}
    missing = [i for i in entity_ids if i not in found]
    assert not missing, f"No {node_type} node for ids {missing}"


def assert_graph_nodes_present(data, *node_ids):
    """Assert every graph node id (for example ``skill_4``) is in the payload."""
    missing = set(node_ids) - graph_node_ids(data)
    assert not missing, f"Missing graph nodes: {sorted(missing)}"


def assert_keys_present(mapping, keys, label="response"):
    """Assert every expected key is present in a response mapping."""
    missing = set(keys) - set(mapping)
    assert not missing, f"Missing keys in {label}: {missing}"


def assert_meta_counts_at_least(meta, minimum, *keys):
    """Assert each meta count key exists and is at least ``minimum``."""
    low = [k for k in keys if k not in meta or meta[k] < minimum]
    assert not low, f"Meta counts below {minimum}: {low}"
