# REST API Reference

Forgetful exposes a REST API for web UI integration and external access.

**Base URL:** `http://localhost:8020/api/v1`

## Authentication

The REST API uses the **same authentication provider** as MCP routes. This means:

- If you've configured OAuth2/OIDC for MCP, the same tokens work for REST
- If auth is disabled (`FASTMCP_SERVER_AUTH` not set), REST endpoints use a default user
- All FastMCP auth providers are supported (JWT, OAuth2, GitHub, Google, Azure, etc.)

### When Auth is Enabled

Include a Bearer token in the Authorization header:

```
Authorization: Bearer <your-oauth-token>
```

The token is validated using `mcp.auth.verify_token()` - the same mechanism used for MCP tool authentication. User identity is extracted from the token's `sub` claim.

### When Auth is Disabled (Default)

If `FASTMCP_SERVER_AUTH` is not set, all requests use a default user. No Authorization header is required.

### Configuration

Authentication is configured via FastMCP environment variables:

```bash
# Example: GitHub OAuth (the provider is selected by its class path)
FASTMCP_SERVER_AUTH=fastmcp.server.auth.providers.github.GitHubProvider
FASTMCP_SERVER_AUTH_GITHUB_CLIENT_ID=your_client_id
FASTMCP_SERVER_AUTH_GITHUB_CLIENT_SECRET=your_client_secret
FASTMCP_SERVER_AUTH_GITHUB_BASE_URL=https://forgetful.example.com

# Example: JWT bearer tokens
FASTMCP_SERVER_AUTH=fastmcp.server.auth.providers.jwt.JWTVerifier
FASTMCP_SERVER_AUTH_JWT_JWKS_URI=https://auth.example.com/.well-known/jwks.json
```

The supported providers are the GitHub, Google, JWT and token-introspection classes; any other value
stops startup with an error listing them. See the [Configuration Reference](./configuration.md) for every
variable, and the [FastMCP Auth Documentation](https://fastmcp.wiki/en/servers/auth/authentication) for provider details.

## Common Response Codes

| Code | Description |
|------|-------------|
| 200 | Success |
| 201 | Created |
| 400 | Bad Request (validation error, malformed or non-object JSON body, `NaN`/infinite numbers in a body, invalid query parameter) |
| 401 | Unauthorized (missing/invalid token) |
| 404 | Not Found (including a path id that is not an integer id, such as `/api/v1/memories/abc`, and a referenced `project_id` that does not exist or belongs to another user) |
| 422 | Unprocessable Entity (`InvalidStateTransitionError` -- for example marking an already obsolete memory obsolete) |
| 500 | Internal Server Error |

## Update Requests (PUT)

`PUT` endpoints for projects
use PATCH semantics: send only the fields you want to change.

- A field that is omitted or sent as `null` is left unchanged. A body that changes nothing returns
  the current record and does not modify `updated_at`.
- `null` never clears a field. Fields such as `project_id`, `source_url` and `confidence` therefore cannot
  be reset through a `PUT`.
- Optional text fields that can be cleared, such as a project's `notes`, are cleared by sending an empty
  string. Lists (`tags`) are replaced by what you send, so `[]` clears them.

---

## Health

### GET /health

Health check endpoint (no authentication required).

**Response:**
```json
{
  "status": "healthy",
  "timestamp": "2024-12-05T10:00:00Z",
  "service": "forgetful",
  "version": "0.3.0"
}
```

---

## Memories

### GET /api/v1/memories

List memories with pagination, sorting, and filtering.

**Query Parameters:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `limit` | int | 20 | Results per page (1-100) |
| `offset` | int | 0 | Skip N results |
| `sort_by` | string | created_at | Sort field: `created_at`, `updated_at`, `importance` |
| `sort_order` | string | desc | Sort direction: `asc`, `desc` |
| `project_id` | int | - | Filter by project (32-bit id; outside -2147483648 to 2147483647 returns 400) |
| `importance_min` | int | - | Minimum importance (1-10) |
| `tags` | string | - | Comma-separated tags |
| `include_obsolete` | bool | false | Include obsolete memories |

**Response:**
```json
{
  "memories": [
    {
      "id": 1,
      "title": "Memory title",
      "content": "Memory content...",
      "importance": 7,
      "tags": ["tag1", "tag2"],
      "created_at": "2024-12-05T10:00:00Z",
      "updated_at": "2024-12-05T10:00:00Z",
      "linked_memory_ids": [2, 3],
      "project_ids": [1],
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
  "project_ids": [1],
  "source_repo": "owner/repo",
  "source_files": ["path/to/file.py"],
  "source_url": "https://example.com/source",
  "confidence": 0.85,
  "encoding_agent": "claude-sonnet-4",
  "encoding_version": "1.0.0"
}
```

**Provenance Fields (all optional):**

| Field | Type | Description |
|-------|------|-------------|
| `source_repo` | string | Repository source (e.g., 'owner/repo', max 200 chars) |
| `source_files` | string[] | List of file paths that informed this memory (each max 4096 chars) |
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
  "id": 1,
  "title": "Memory title",
  "linked_memory_ids": [],
  "project_ids": [1],
  "similar_memories": []
}
```

**Errors:** `404` with a message such as `Projects not found: {999}` when an id in `project_ids` does not exist (or belongs to another user). Nothing is created in that case.

### PUT /api/v1/memories/{id}

Update an existing memory.

**Request Body:** (all fields optional)
```json
{
  "title": "Updated title",
  "content": "Updated content",
  "importance": 8,
  "tags": ["updated-tag"],
  "source_repo": "owner/repo",
  "source_files": ["path/to/file.py"],
  "source_url": "https://example.com/source",
  "confidence": 0.9,
  "encoding_agent": "manual-review",
  "encoding_version": "1.0.0"
}
```

Provenance fields can be added or updated after memory creation. See POST /api/v1/memories for field descriptions.

A field that is omitted or `null` is left unchanged. The link list `project_ids` replaces the existing links when provided, and an empty list removes them all.

**Response:** Updated memory object

**Errors:** `404` with `Memory not found` when the memory does not exist, or with a message such as `Projects not found: {999}` when an associated id does not exist.

### DELETE /api/v1/memories/{id}

Mark a memory as obsolete (soft delete).

**Request Body:** (optional)
```json
{
  "reason": "No longer relevant",
  "superseded_by": 42
}
```

**Response:**
```json
{
  "success": true
}
```

**Errors:**
- `400` when `superseded_by` is not an integer.
- `404` with `Memory not found` when the memory does not exist, or with `Superseding memory {id} not found` when `superseded_by` does not reference one of your memories.
- `422` when the memory is already obsolete (restore it first; the original reason and timestamp are kept) or when `superseded_by` is the memory's own id.

### POST /api/v1/memories/search

Semantic search across memories. A blank (whitespace-only) `query` is rejected with `400`.

**Request Body:**
```json
{
  "query": "search query",
  "query_context": "why searching",
  "k": 10,
  "include_links": true,
  "importance_threshold": 5,
  "project_ids": [1, 2],
  "token_context_threshold": 4000
}
```

`token_context_threshold` (optional, 4000-25000) lowers the token budget for this request. It can
never exceed the server's `MEMORY_TOKEN_BUDGET`, which applies when the field is omitted.

**Response:**
```json
{
  "memories": [...],
  "total": 10
}
```

`truncated` is `true` when memories were dropped because of the token budget or `MEMORY_MAX_MEMORIES`.

### POST /api/v1/memories/{id}/links

Link memories together (bidirectional).

**Request Body:**
```json
{
  "related_ids": [2, 3, 4]
}
```

`related_ids` must be a non-empty list of integers and must not contain the memory's own id;
anything else returns `400`.

**Response:**
```json
{
  "linked_ids": [2, 3, 4]
}
```

### GET /api/v1/memories/{id}/links

Get memories linked to this memory.

**Query Parameters:**
- `limit` (int, default 20): Max linked memories to return. Must be at least 1 (`400` otherwise); values above 100 are capped at 100
- `include_obsolete` (bool, default false): Include linked memories that have been marked obsolete

**Response:**
```json
{
  "memory_id": 1,
  "linked_memories": [...]
}
```

### DELETE /api/v1/memories/{id}/links/{target_id}

Remove a link between two memories (bidirectional).

**Response:**
```json
{
  "success": true
}
```

---

## Projects

### GET /api/v1/projects

List all projects.

**Query Parameters:**

| Parameter | Type | Description |
|-----------|------|-------------|
| `status` | string | Filter by status: `active`, `archived`, `completed` |
| `repo_name` | string | Match equivalent repository addresses or literal identifiers |

Repository lookup recognises equivalent HTTPS, SSH, and host-qualified addresses. For example,
`github.com/ScottRBK/forgetful`, `https://github.com/ScottRBK/forgetful.git`, and
`git@github.com:ScottRBK/forgetful.git` match each other. A trailing slash is optional.
Hostnames are case-insensitive; repository paths and explicit ports remain significant.
Hostless paths such as `group/subgroup/repo` do not imply GitHub or another host.
Unsupported or ambiguous forms use literal matching. Lookup returns every matching project and
can be combined with the status filter; it does not perform partial-name or fuzzy search.

**Response:** `ProjectSummary` items (lightweight; no `description` or `notes`).

```json
{
  "projects": [
    {
      "id": 1,
      "name": "Project Name",
      "project_type": "development",
      "status": "active",
      "repo_name": "owner/repo",
      "last_encoding_point": "5d41402abc4b2a76b9719d911017c592",
      "memory_count": 12,
      "created_at": "2024-12-05T10:00:00Z",
      "updated_at": "2024-12-05T10:00:00Z"
    }
  ],
  "total": 5
}
```

### GET /api/v1/projects/{id}

Get a single project by ID.

**Response:** Full `Project` object (includes `description`, `notes`, provenance fields, `memory_count`, and `last_encoding_point` when set). Shape differs from list `ProjectSummary` items.

### POST /api/v1/projects

Create a new project.

`repo_name` is an optional Git repository identifier or address (maximum 255 characters).
Nested paths, host-qualified identifiers, HTTPS/SSH addresses, and opaque values are accepted.
Surrounding whitespace is trimmed; the supplied format is otherwise preserved in responses.
An empty string clears the repository when updating a project. Invalid project fields return
HTTP 400 with JSON field locations and error messages.

**Request Body:**
```json
{
  "name": "Project Name",
  "description": "Project description",
  "project_type": "development",
  "repo_name": "owner/repo",
  "last_encoding_point": "5d41402abc4b2a76b9719d911017c592"
}
```

**Project Types:** `personal`, `work`, `learning`, `development`, `infrastructure`, `template`, `product`, `documentation`, `open-source`

**`last_encoding_point`:** Opaque, caller-managed checkpoint through which memories were last encoded for this project (e.g. a Git commit SHA). The server never infers or advances it; the encoding client updates it only after a successful run.

### PUT /api/v1/projects/{id}

Update an existing project.

**Request Body:** (all fields optional)
```json
{
  "name": "Updated Name",
  "description": "Updated description",
  "status": "completed",
  "last_encoding_point": "7d793037a0760186574b0282f2ebc2eb"
}
```

Omit `last_encoding_point` to leave the stored value unchanged; send an empty string to clear it.

### DELETE /api/v1/projects/{id}

Delete a project (preserves associated memories).

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
bodies (`memory_id`) must be JSON integers that fit
too. Anything else returns HTTP 400 instead of reaching the database.
