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
      "encoding_version": "1.0.0",
      "access_count": 0,
      "last_accessed_at": null
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

The object includes server-managed `access_count` (int, default 0) and `last_accessed_at`
(ISO datetime or null). Callers cannot set them on POST/PUT; incrementing them does not change
`updated_at`. On upstream, counters increment only when `memory.read` / `memory.queried` events fire
(`get_memory` / `query_memory`), which requires `ACTIVITY_TRACK_READS=true` and a wired EventBus
(default off → counters stay 0). REST listing does not increment them.

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

## Activity

> **Experimental Feature**
>
> Activity logging is an experimental feature. The async event-driven architecture may cause
> issues with SQLite backends due to connection pooling conflicts. **If you are using SQLite
> and do not intend to use activity tracking, leave it disabled by not configuring the
> activity-related settings.** PostgreSQL users can enable this feature but please be advised it is still experimental 
>
> Known limitations:
> - SQLite in-memory mode (testing): Events may conflict with concurrent database operations

The Activity API provides read access to the activity log, which tracks all memory, project and link lifecycle events (created, updated, deleted) and optionally read/query operations.

### Configuration

Activity tracking is controlled by these settings:

| Setting | Type | Default | Description |
|---------|------|---------|-------------|
| `ACTIVITY_RETENTION_DAYS` | int | None | Days to keep events (None = forever). Cleanup happens lazily on API access. |
| `ACTIVITY_TRACK_READS` | bool | false | Track read/query operations (opt-in, can be high volume) |

### GET /api/v1/activity

List activity events with filtering and pagination.

**Query Parameters:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `entity_type` | string | - | Filter by type: `memory`, `project`, `link` |
| `action` | string | - | Filter by action: `created`, `updated`, `deleted`, `read`, `queried` |
| `entity_id` | int | - | Filter by specific entity ID |
| `actor` | string | - | Filter by actor: `user`, `system`, `llm-maintenance` |
| `since` | datetime | - | Only events after this timestamp (ISO 8601) |
| `until` | datetime | - | Only events before this timestamp (ISO 8601) |
| `limit` | int | 50 | Results per page (1-100) |
| `offset` | int | 0 | Skip N results |

`since` and `until` are compared in UTC: a value without an offset is read as UTC and a value with an
offset (`+02:00`) is converted to UTC. Encode a `+` in the offset as `%2B`, since a raw `+` reaches the
server as a space. A malformed `since`, `until`, `limit`, `offset` or `entity_id` returns 400 instead of
being ignored, and so does an `entity_id` outside the 32-bit range (-2147483648 to 2147483647, the size
of the stored id) or a `limit`/`offset` outside the 64-bit range. The `project_id` filter of
`GET /api/v1/memories` follows the same 32-bit rule.

**Response:**
```json
{
  "events": [
    {
      "id": 123,
      "entity_type": "memory",
      "entity_id": 1,
      "action": "updated",
      "changes": {
        "title": {"old": "Old Title", "new": "New Title"},
        "importance": {"old": 5, "new": 8}
      },
      "snapshot": {
        "id": 1,
        "title": "New Title",
        "content": "...",
        "importance": 8
      },
      "actor": "user",
      "actor_id": null,
      "metadata": null,
      "created_at": "2026-01-06T12:00:00Z"
    }
  ],
  "total": 42,
  "limit": 50,
  "offset": 0
}
```

### GET /api/v1/activity/{entity_type}/{entity_id}

Get activity history for a specific entity.

**Path Parameters:**

| Parameter | Type | Description |
|-----------|------|-------------|
| `entity_type` | string | Entity type: `memory`, `project` |
| `entity_id` | int | Entity ID |

**Query Parameters:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `limit` | int | 50 | Results per page (1-100) |
| `offset` | int | 0 | Skip N results |

**Response:** Same format as GET /api/v1/activity

### Event Types

**Entity Types:**
- `memory` - Knowledge memories
- `project` - Project containers
- `link` - Memory-to-memory connections

**Action Types:**
- `created` - Entity was created
- `updated` - Entity was modified (includes `changes` diff)
- `deleted` - Entity was soft-deleted (obsolete)
- `read` - Entity was read (if `ACTIVITY_TRACK_READS=true`)
- `queried` - Search was performed (if `ACTIVITY_TRACK_READS=true`)

**Actor Types:**
- `user` - Human user action
- `system` - Automated system action
- `llm-maintenance` - LLM-based maintenance task (future)

### Changes Format

For `updated` events, the `changes` field contains a diff:

```json
{
  "changes": {
    "field_name": {
      "old": "previous value",
      "new": "current value"
    }
  }
}
```

Only modified fields are included. The `snapshot` field contains the full entity state after the change.

### Link Events

Link events (`entity_type: "link"`) use `entity_id: 0` and store source/target in metadata:

```json
{
  "entity_type": "link",
  "entity_id": 0,
  "action": "created",
  "snapshot": {"source_id": 1, "target_id": 2},
  "metadata": {"source_id": 1, "target_id": 2}
}
```

### GET /api/v1/activity/stream

Stream activity events in real-time via Server-Sent Events (SSE).

Events are filtered to only those belonging to the authenticated user. Each event includes a sequence number for gap detection and client recovery.

**Query Parameters:**

| Parameter | Type | Description |
|-----------|------|-------------|
| `entity_type` | string | Filter by type (optional) |
| `action` | string | Filter by action (optional) |

**Response:** SSE stream with `text/event-stream` content type.

**Event Format:**
```
event: activity
data: {"seq": 1, "entity_type": "memory", "action": "created", "entity_id": 42, ...}

event: activity
data: {"seq": 2, "entity_type": "memory", "action": "updated", "entity_id": 42, ...}
```

**Sequence Numbers:**

Each event includes a monotonically increasing `seq` field per user. Clients should track this to detect gaps (e.g., receiving seq 45 when last seen was 42 indicates missed events).

When the stream is opened with `entity_type` and/or `action` filters, `seq` counts only the events
that match them, so events excluded by a filter never appear as a gap and, while the client stays
connected, a gap always means a dropped event. Events that match while the client is disconnected are
not counted on its behalf: after a reconnect, recover them with the REST API as described below.

**Gap Recovery:**

On gap detection, fetch missed events via the REST API:
```
GET /api/v1/activity?since=<last_seen_timestamp>&limit=100
```

**Configuration:**

| Setting | Type | Default | Description |
|---------|------|---------|-------------|
| `SSE_MAX_QUEUE_SIZE` | int | 1000 | Max events per subscriber queue (backpressure) |

When the queue is full, new events are dropped with a warning log. Clients can detect this via sequence gaps and resync via REST.

**Example Usage (JavaScript):**
```javascript
const events = new EventSource('/api/v1/activity/stream');
let lastSeq = 0;

events.addEventListener('activity', (e) => {
  const event = JSON.parse(e.data);

  // Gap detection
  if (lastSeq > 0 && event.seq > lastSeq + 1) {
    console.warn(`Gap detected: ${lastSeq} -> ${event.seq}`);
    // Fetch missed events via REST API
  }

  lastSeq = event.seq;
  handleEvent(event);
});
```

**Error Responses:**

| Code | Description |
|------|-------------|
| 400 | Invalid filter parameter |
| 401 | Unauthorized |
| 503 | Activity streaming not enabled (`ACTIVITY_ENABLED=false`) |

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
