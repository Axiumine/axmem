# REST API Reference

Axmem exposes a REST API for web UI integration and external access.

**Base URL:** `http://localhost:8020/api/v1`

## Authentication

The REST API uses the **same authentication provider** as the MCP routes, chosen by `AUTH_MODE`. The
server fails closed: with `AUTH_MODE` unset it does not start (see the
[Configuration Reference](./configuration.md#authentication-configuration)).

| `AUTH_MODE` | What the REST API requires |
|---|---|
| `jwt` | `Authorization: Bearer <JWT>`; signature, issuer, audience and expiry are checked (the server is configured with an issuer and an audience, and a token without an `exp` claim is refused) |
| `introspection` | `Authorization: Bearer <opaque token>`; the token must be active at the introspection endpoint, and when an audience is configured its `aud` must name it |
| `static` | `Authorization: Bearer <token>`; the token must match a configured tenant's SHA-256 hash |
| `none` | Nothing; every request is the `DEFAULT_TENANT` tenant. Accepted only on a loopback bind, where a request with a `Host` that is not a loopback name (or one listed in `AXMEM_ALLOWED_HOSTS`) answers `421` and one with a foreign `Origin` answers `403` |

### Tenants

Every request acts as one **tenant**, read from the verified token: the claim `tenant_id`, else `tenant`,
else `sub`. For a static token it is the tenant that token is configured for. A tenant reads and changes only
its own memories: another tenant's memory id answers `404 Memory not found`, exactly like an id that does not
exist, and listing and search never return it.

### Bearer tokens

```
Authorization: Bearer <token>
```

A missing header, a malformed header, and a token that is invalid, expired or inactive all answer `401`
with `{"error": "..."}`. A token that names no tenant (none of the three claims) is refused as well. Tokens
are never logged or echoed back.

### Scopes and routes

The instance ceiling (`AXMEM_SCOPES`) and the scopes of the token limit the REST routes as they limit the MCP
tools. Each route stands for one tool, and answers `403` (`Tool '<tool>' is not permitted under current scopes.
Required scope: '<scope>'`) when that tool is not permitted, after the credential has been accepted:

| Route | Tool | Scope needed |
|---|---|---|
| `GET /api/v1/memories` | `get_recent_memories` | `read:memories` |
| `GET /api/v1/memories/{id}` | `get_memory` | `read:memories` |
| `GET /api/v1/memories/{id}/links` | `get_memory` | `read:memories` |
| `POST /api/v1/memories/search` | `query_memory` | `read:memories` |
| `POST /api/v1/memories` | `create_memory` | `write:memories` |
| `PUT /api/v1/memories/{id}` | `update_memory` | `write:memories` |
| `DELETE /api/v1/memories/{id}` | `mark_memory_obsolete` | `write:memories` |
| `POST /api/v1/memories/{id}/links` | `link_memories` | `write:memories` |
| `DELETE /api/v1/memories/{id}/links/{target_id}` | `unlink_memories` | `write:memories` |

`GET /health`, `GET /api/v1/auth/info` and the service information at `/` read no memory and need no scope. How
the scopes of a token are read is described in the
[Configuration Reference](./configuration.md#security-best-practices).

### Request bodies

A request that carries a body must declare `Content-Type: application/json` (or an `application/*+json` type);
anything else answers `415` before the body is read. A request without a body needs no content type.

A body is also limited in size: one above `REQUEST_BODY_MAX_BYTES` (4 MiB by default, REST routes and `/mcp` alike) is
answered `413` with `{"error": "The request body is larger than the limit of <n> bytes"}`. A declared `Content-Length`
above the limit is refused without reading the body, and a chunked body is refused as soon as it passes the limit.
A request that is refused for its size changes nothing: the route is not run on the part of the body that was read, and
`DELETE /api/v1/memories/{id}`, whose body is optional, does not treat a body that was cut off as an empty one.

### Limits of the fields

A field above its limit answers `400` (the settings are in the [Configuration Reference](./configuration.md)):
`query` and `query_context` of a search (`MEMORY_QUERY_MAX_LENGTH`, `MEMORY_QUERY_CONTEXT_MAX_LENGTH`), the number of
`source_files` of a memory (`MEMORY_SOURCE_FILES_MAX_COUNT`), the number of `related_ids` of a link request
(`MEMORY_LINKS_MAX_PER_REQUEST`) and the `reason` of `DELETE /api/v1/memories/{id}`, which must be a text that is not
blank (`MEMORY_OBSOLETE_REASON_MAX_LENGTH`; a number or an object is refused and the memory stays active).

### GET /api/v1/auth/info

Public endpoint (no credential) for clients to detect the mode before sending one:

```json
{"authEnabled": true, "authMode": "static"}
```

`authMode` is `disabled` (`AUTH_MODE=none`, `authEnabled` is `false`), `jwt`, `introspection` or `static`.

## Common Response Codes

| Code | Description |
|---|---|
| 200 | Success |
| 201 | Created |
| 400 | Bad Request (validation error, malformed or non-object JSON body, `NaN`/infinite numbers in a body, invalid query parameter) |
| 401 | Unauthorized (missing, invalid or expired token) |
| 403 | Forbidden (the instance ceiling or the token's scopes do not permit the tool the route stands for; on a server without a credential, a request with a foreign `Origin`) |
| 404 | Not Found (including a path id that is not a UUID, such as `/api/v1/memories/abc`, and a memory that belongs to another tenant) |
| 409 | Conflict (concurrent changes to the same memory kept an update or an obsolete mark from being applied; or, on a cluster, the replicas kept holding different versions of a memory the request reads or links, or the outcome of a write could not be told; the request can be repeated, and after an unknown outcome the memory should be read first) |
| 413 | Content Too Large (a request body above `REQUEST_BODY_MAX_BYTES`) |
| 415 | Unsupported Media Type (a body that is not declared as `application/json`) |
| 421 | Misdirected Request (on a server without a credential, a `Host` that is not `localhost` or a loopback address) |
| 422 | Unprocessable Entity (`InvalidStateTransitionError` -- for example marking an already obsolete memory obsolete) |
| 500 | Internal Server Error |

## Update Requests (PUT)

`PUT /api/v1/memories/{id}` uses PATCH semantics: send only the fields you want to change.

- A field that is omitted or sent as `null` is left unchanged. A body that changes nothing returns
  the current record and does not modify `updated_at`.
- `null` never clears a field. Fields such as `source_url` and `confidence` therefore cannot
  be reset through a `PUT`.
- The `project` scope is cleared by sending an empty string. Lists (`tags`) are replaced by what you
  send, so `[]` clears them.

---

## Health

### GET /health

Health check endpoint (no authentication required). It answers `200` while the service can serve and `503` otherwise:
while it starts (`"status": "initialising"`) and after a local TensorRT model met a CUDA error that it cannot recover
from (`"status": "unhealthy"`: every later embedding or rerank call fails until the process restarts, so a probe or a
supervisor should restart the server).

**Response:**
```json
{
  "status": "healthy",
  "timestamp": "2024-12-05T10:00:00Z",
  "service": "axmem",
  "version": "0.3.0"
}
```

**Response (503, a model can no longer serve):**
```json
{
  "status": "unhealthy",
  "message": "A model can no longer serve; restart the server",
  "failures": ["the embedder engine is unusable after an earlier failure (cudaErrorIllegalAddress: ...); restart the server"],
  "timestamp": "2024-12-05T10:00:00Z",
  "service": "axmem",
  "version": "0.3.0"
}
```

---

## Memories

### GET /api/v1/memories

List memories with pagination, sorting, and filtering.

**Query Parameters:**

| Parameter | Type | Default | Description |
|---|---|---|---|
| `limit` | int | 20 | Results per page (1-100) |
| `offset` | int | 0 | Skip N results |
| `sort_by` | string | created_at | Sort field: `created_at`, `updated_at`, `importance` |
| `sort_order` | string | desc | Sort direction: `asc`, `desc` |
| `project` | string | - | Only memories in this project scope (trimmed, 1 to 500 characters; a blank or longer value returns 400) |
| `importance_min` | int | - | Minimum importance (1-10) |
| `tags` | string | - | Comma-separated tags |
| `include_obsolete` | bool | false | Include obsolete memories |

**Response:**
```json
{
  "memories": [
    {
      "id": "3f2b8c1e-5d4a-4e7b-9c6a-1b2d3e4f5a6b",
      "title": "Memory title",
      "content": "Memory content...",
      "importance": 7,
      "tags": ["tag1", "tag2"],
      "created_at": "2024-12-05T10:00:00Z",
      "updated_at": "2024-12-05T10:00:00Z",
      "linked_memory_ids": ["7a9d0e12-3b4c-4d5e-8f60-718293a4b5c6", "c1d2e3f4-a5b6-4c7d-8e9f-0a1b2c3d4e5f"],
      "project": "billing",
      "source_repo": "owner/repo",
      "source_files": ["path/to/file.py"],
      "source_url": "https://example.com/source",
      "confidence": 0.85,
      "encoding_agent": "claude-sonnet-4",
      "encoding_version": "1.0.0"
    }
  ],
  "total": 42,
  "limit": 20,
  "offset": 0
}
```

### GET /api/v1/memories/{id}

Get a single memory by ID.

**Response:** Memory object (see above)

**Errors:** `404` when the memory does not exist or belongs to another tenant. `409` when the replicas of a cluster kept holding different versions of the memory (it is being rewritten at that moment); repeat the request.

### POST /api/v1/memories

Create a new memory.

**Request Body:**
```json
{
  "title": "Memory title",
  "content": "Memory content...",
  "context": "Why this is being stored",
  "keywords": ["keyword1", "keyword2"],
  "tags": ["tag1"],
  "importance": 7,
  "project": "billing",
  "source_repo": "owner/repo",
  "source_files": ["path/to/file.py"],
  "source_url": "https://example.com/source",
  "confidence": 0.85,
  "encoding_agent": "claude-sonnet-4",
  "encoding_version": "1.0.0"
}
```

**Project scope (optional):** `project` is a free-form label, trimmed, 1 to 500 characters. The scope is
created the first time it is used, and each tenant has its own scopes. A blank or longer label returns `400`.

**Provenance Fields (all optional):**

| Field | Type | Description |
|---|---|---|
| `source_repo` | string | Repository source (e.g., 'owner/repo', max 200 chars) |
| `source_files` | string[] | List of file paths that informed this memory (at most 50 paths, each max 4096 chars) |
| `source_url` | string | URL to original source material (max 2048 chars) |
| `confidence` | float | Encoding confidence score (0.0-1.0) |
| `encoding_agent` | string | Agent/process that created this memory (max 100 chars) |
| `encoding_version` | string | Version of encoding process/prompt (max 50 chars) |

**Validation:** `title` and `content` must not be blank (whitespace only); each `keywords` and
`tags` item is limited to 100 characters (`MEMORY_KEYWORD_MAX_LENGTH`, `MEMORY_TAG_MAX_LENGTH`);
strings that cannot be encoded as UTF-8, such as a lone surrogate from a `"\ud83d"` escape, are
rejected. Invalid requests return `400` with a list of errors (`loc`, `msg`, `type`).
`PUT /api/v1/memories/{id}` applies the same rules to the fields it receives, and also rejects a
blank `context`.

**Response (201):**
```json
{
  "id": "3f2b8c1e-5d4a-4e7b-9c6a-1b2d3e4f5a6b",
  "title": "Memory title",
  "linked_memory_ids": [],
  "project": "billing",
  "similar_memories": [],
  "obsolete_matches": []
}
```

`linked_memory_ids` are the memories the new one was linked to automatically, and `similar_memories` summarises
each of them (`id`, `title`, `keywords`, `tags`, `importance`, the two timestamps and `similarity`).
`obsolete_matches` is always present: it lists the obsolete memories whose similarity reaches
`OBSOLETE_WARNING_THRESHOLD` (each with `id`, `title`, `similarity`, `obsolete_reason`, `superseded_by`,
`obsoleted_at` and `project`), and stays `[]` when `OBSOLETE_WARNING_ENABLED` is false.

### PUT /api/v1/memories/{id}

Update an existing memory.

**Request Body:** (all fields optional)
```json
{
  "title": "Updated title",
  "content": "Updated content",
  "importance": 8,
  "tags": ["updated-tag"],
  "project": "billing",
  "source_repo": "owner/repo",
  "source_files": ["path/to/file.py"],
  "source_url": "https://example.com/source",
  "confidence": 0.9,
  "encoding_agent": "manual-review",
  "encoding_version": "1.0.0"
}
```

Provenance fields can be added or updated after memory creation. See POST /api/v1/memories for field descriptions.

A field that is omitted or `null` is left unchanged. `project` replaces the memory's scope when provided, and an empty string removes the memory from its scope.

**Response:** Updated memory object

**Errors:** `404` with `Memory not found` when the memory does not exist or belongs to another tenant. `409` when concurrent changes to
the same memory kept the update from being applied after a few attempts, or when the outcome of the write could not be told (the message says so, and the update may have been applied: read the memory first); repeat the request.

### DELETE /api/v1/memories/{id}

Mark a memory as obsolete (soft delete).

**Nothing is deleted: the memory stays stored and is left out of normal queries and automatic links. No REST route brings it back; the `unobsolete_memory` tool does. No tool or REST route deletes a memory for good.**

**Request Body:** (optional)
```json
{
  "reason": "No longer relevant",
  "superseded_by": "0e9d8c7b-6a5f-4e3d-9c2b-1a0f9e8d7c6b"
}
```

**Response:**
```json
{
  "success": true
}
```

**Errors:**
- `400` when `superseded_by` is not a UUID memory id, or when `reason` is not a text (a number, an object), is blank or is longer than `MEMORY_OBSOLETE_REASON_MAX_LENGTH` (500) characters; the memory stays active.
- `404` with `Memory not found` when the memory does not exist, or with `Superseding memory {id} not found` when `superseded_by` does not reference one of your memories.
- `409` when concurrent changes kept the mark from being applied; repeat the request.
- `422` when the memory is already obsolete (restore it first; the original reason and timestamp are kept) or when `superseded_by` is the memory's own id.

### POST /api/v1/memories/search

Semantic search across memories. A blank (whitespace-only) `query` is rejected with `400`, and so is a `query` or `query_context` longer than `MEMORY_QUERY_MAX_LENGTH` or `MEMORY_QUERY_CONTEXT_MAX_LENGTH` (2000 characters each).

**Request Body:**
```json
{
  "query": "search query",
  "query_context": "why searching",
  "k": 10,
  "include_links": true,
  "importance_threshold": 5,
  "project": "billing",
  "token_context_threshold": 4000
}
```

`token_context_threshold` (optional, 4000-25000) lowers the token budget for this request. It can
never exceed the server's `MEMORY_TOKEN_BUDGET`, which applies when the field is omitted.

**Response:**
```json
{
  "query": "search query",
  "primary_memories": [...],
  "linked_memories": [{"memory": {...}, "link_source_id": "3f2b8c1e-5d4a-4e7b-9c6a-1b2d3e4f5a6b"}],
  "scores": [{"memory_id": "3f2b8c1e-5d4a-4e7b-9c6a-1b2d3e4f5a6b", "similarity": 0.83, "rerank_score": 0.97}],
  "total_count": 10,
  "token_count": 1520,
  "truncated": false
}
```

`primary_memories` are memory objects as above (sorted by importance, then by search order), `scores` has one
entry per primary memory in the same order (`rerank_score` is `null` unless reranking is enabled), and each
entry of `linked_memories` names the primary it hangs from in `link_source_id`. `total_count` counts the primary and the linked
memories returned and `token_count` the tokens they use. `truncated` is `true` when memories were dropped because of the token
budget or `MEMORY_MAX_MEMORIES`.

### POST /api/v1/memories/{id}/links

Link memories together (bidirectional).

**Request Body:**
```json
{
  "related_ids": ["7a9d0e12-3b4c-4d5e-8f60-718293a4b5c6", "c1d2e3f4-a5b6-4c7d-8e9f-0a1b2c3d4e5f", "0e9d8c7b-6a5f-4e3d-9c2b-1a0f9e8d7c6b"]
}
```

`related_ids` must be a non-empty list of UUID memory ids and must not contain the memory's own id;
anything else returns `400`.

**Response:**
```json
{
  "linked_ids": ["7a9d0e12-3b4c-4d5e-8f60-718293a4b5c6", "c1d2e3f4-a5b6-4c7d-8e9f-0a1b2c3d4e5f", "0e9d8c7b-6a5f-4e3d-9c2b-1a0f9e8d7c6b"]
}
```

`409` when the replicas of a cluster kept holding different versions of the memory or of one of the targets; nothing is linked, repeat the request.

### GET /api/v1/memories/{id}/links

Get memories linked to this memory.

**Query Parameters:**
- `limit` (int, default 20): Max linked memories to return. Must be at least 1 (`400` otherwise); values above 100 are capped at 100
- `include_obsolete` (bool, default false): Include linked memories that have been marked obsolete

**Response:**
```json
{
  "memory_id": "3f2b8c1e-5d4a-4e7b-9c6a-1b2d3e4f5a6b",
  "linked_memories": [...]
}
```

`409` when the replicas of a cluster kept holding different versions of the memory or of one of its linked memories (the list is never shortened for it); repeat the request.

### DELETE /api/v1/memories/{id}/links/{target_id}

Remove a link between two memories (bidirectional).

**The link is deleted and this cannot be undone; both memories stay as they are. To get the link back, create it again (`POST /api/v1/memories/{id}/links`).**

**Response:**
```json
{
  "success": true
}
```

`409` when the replicas of a cluster kept holding different versions of the memory; the link stays, repeat the request.

---

## Projects

There are no project routes. A project is an optional scope label on a memory (the `project` field of
`POST /api/v1/memories`, `PUT /api/v1/memories/{id}` and `POST /api/v1/memories/search`, and the `project`
query parameter of `GET /api/v1/memories`). The routes `GET`, `POST`, `PUT` and `DELETE` under
`/api/v1/projects` are removed.

---

## Error Responses

All error responses follow this format:

```json
{
  "error": "Error message describing what went wrong"
}
```

For validation errors (400), the error may contain detailed field-level errors:

```json
{
  "error": [
    {
      "loc": ["body", "title"],
      "msg": "field required",
      "type": "value_error.missing"
    }
  ]
}
```

Each entry contains only `type`, `loc` and `msg`; the rejected input value is not echoed back.
Integer query parameters must fit in a signed 64-bit integer, and the identifiers in link request
bodies (`related_ids`) must be UUID strings. Anything else returns HTTP 400 instead of reaching the store.
