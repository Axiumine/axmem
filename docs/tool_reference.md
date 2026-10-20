# Forgetful MCP Server - Complete Tool Reference

This guide provides comprehensive documentation for all tools available in the Forgetful MCP server.

---

## Table of Contents

- [Meta-Tools Pattern](#meta-tools-pattern)
- [Tool Categories Overview](#tool-categories-overview)
- [User Tools](#user-tools)
- [Memory Tools](#memory-tools)
- [Project Tools](#project-tools)
- [Cross-Category Workflows](#cross-category-workflows)

---

## Meta-Tools Pattern

Forgetful uses a **meta-tools pattern** to preserve your LLM's context window. Only
**3 meta-tools** are visible to MCP clients; the inner tool catalog depends on the permitted
scopes.

### The Three Meta-Tools

#### 1. `discover_forgetful_tools`
List available tools, optionally filtered by category.

**Parameters:**
- `category` (optional): Filter by category (`user`, `memory`, `project`) or by `linking`, which is not a category of its own but selects the link and unlink tools of the memory category. An unknown value is rejected with the list of valid categories

**Returns:**
- `tools_by_category`: Tools grouped by category
- `total_count`: Total number of tools
- `categories_available`: List of all categories
- `filtered_by`: Applied filter (if any)

**Example:**
```python
# Discover all memory tools
discover_forgetful_tools(category="memory")

# Discover all available tools
discover_forgetful_tools()
```

#### 2. `how_to_use_forgetful_tool`
Get detailed documentation for a specific tool.

**Parameters:**
- `tool_name`: Name of the tool

**Returns:**
- Complete tool documentation with JSON schema, parameters, and examples

**Example:**
```python
how_to_use_forgetful_tool(tool_name="create_memory")
```

#### 3. `execute_forgetful_tool`
Execute any registered tool dynamically.

**Parameters:**
- `tool_name`: Name of the tool to execute
- `arguments`: Dictionary of arguments for the tool

**Returns:**
- Tool execution result (format depends on specific tool)

**Errors:**
- Argument names must be identifiers (letters, digits and underscores, not starting with a digit); any other
  name is rejected before the tool runs.
- Domain and validation errors (not found, conflict, invalid state, bad arguments) are returned with their message.
- A database constraint violation is reported as "the request violates a data constraint", invalid YAML as
  "invalid YAML in the supplied content", and any other unexpected error as "an internal error occurred; see the
  server logs for details". SQL, bound parameters and driver text never reach the client; they stay in the server log.

**Example:**
```python
execute_forgetful_tool(
    tool_name="create_memory",
    arguments={
        "title": "Database choice: PostgreSQL",
        "content": "Selected PostgreSQL for pgvector support",
        "context": "Choosing the database for vector search",
        "keywords": ["postgresql", "pgvector"],
        "tags": ["database", "decision"],
        "importance": 9
    }
)
```

---

## Tool Categories Overview

The catalog covers users, memories and projects. Call
`discover_forgetful_tools()` for the exact catalog exposed by the running server.

---

## User Tools

Manage user authentication and profile information.

### `get_current_user`

Returns information about the currently authenticated user.

**Parameters:** None

**Returns:**
- `user_id`: Unique user identifier
- `username`: User's username
- `email`: User's email
- `notes`: User profile notes
- `created_at`: Account creation timestamp

**Example:**
```python
user = execute_forgetful_tool("get_current_user", {})
# Returns: {"user_id": 1, "username": "alex_smith", "email": "alex@example.com", ...}
```

### `update_user_notes`

Update the notes field for the current user's profile.

**Parameters:**
- `notes`: Text content for user notes

**Returns:**
- Updated user object

**Example:**
```python
execute_forgetful_tool(
    "update_user_notes",
    {"notes": "Prefers TypeScript over JavaScript. Works on microservices architecture."}
)
```

---

## Memory Tools

The core of Forgetful - atomic knowledge storage and semantic retrieval.

### `create_memory`

Create an atomic memory with automatic linking to related memories.

**Parameters:**
- `title` (required): Short, searchable title (max 200 chars)
- `content` (required): Memory content - ONE concept (max 2000 chars, ~300-400 words)
- `importance` (required): Importance score 1-10 (9-10 = foundational, 7-8 = patterns, 5-6 = context)
- `context` (required): Why the memory matters (max 500 chars)
- `keywords` (required): Search keywords (max 10, each max 100 chars)
- `tags` (required): Categorization tags (max 10, each max 100 chars)
- `project_ids` (optional): Project IDs to link

**Provenance Tracking (optional):**
- `source_repo` (optional): Repository source (e.g., 'owner/repo', max 200 chars)
- `source_files` (optional): List of file paths that informed this memory (each max 4096 chars)
- `source_url` (optional): URL to original source material (max 2048 chars)
- `confidence` (optional): Encoding confidence score (0.0-1.0)
- `encoding_agent` (optional): Agent/process that created this memory (max 100 chars)
- `encoding_version` (optional): Version of encoding process/prompt (max 50 chars)

Blank (whitespace-only) `title` and `content` values are rejected, as are strings that cannot be
encoded as UTF-8 (for example a lone surrogate).

**Returns:**
- `id`: Created memory ID
- `title`: Created memory title
- `linked_memory_ids`: Automatically linked memory IDs
- `similar_memories`: Summaries of the automatic-link candidates; each entry includes `similarity` (cosine similarity to the new memory)
- `obsolete_matches`: Up to 3 obsolete memories similar to the new content (on by default via `OBSOLETE_WARNING_ENABLED`); each match includes `id`, `title`, `similarity`, `obsolete_reason`, `superseded_by`, `obsoleted_at`, `project_ids`. Warning-only; never blocks creation. Empty when the feature is disabled.

**Example:**
```python
memory = execute_forgetful_tool(
    "create_memory",
    {
        "title": "API rate limiting: 100 req/min per user",
        "content": "Implemented rate limiting at 100 requests per minute per authenticated user to prevent abuse. Uses Redis for distributed counting across instances.",
        "importance": 8,
        "context": "Performance and security discussion during API redesign",
        "keywords": ["rate-limiting", "api", "redis", "performance"],
        "tags": ["api", "security", "performance"],
        "project_ids": [12]
    }
)
# Returns: {"id": 156, "linked_memory_ids": [142, 148, 151], ...}
```

**Example with provenance:**
```python
# Memory created by AI agent with source tracking
memory = execute_forgetful_tool(
    "create_memory",
    {
        "title": "FastAPI dependency injection pattern",
        "content": "Use Depends() for request-scoped dependencies.",
        "context": "Recording a reusable FastAPI implementation pattern",
        "keywords": ["fastapi", "depends", "dependency-injection"],
        "tags": ["fastapi", "pattern", "dependency-injection"],
        "importance": 8,
        "source_repo": "tiangolo/fastapi",
        "source_files": ["docs/tutorial/dependencies.md", "docs/advanced/async-database.md"],
        "source_url": "https://fastapi.tiangolo.com/tutorial/dependencies/",
        "confidence": 0.92,
        "encoding_agent": "claude-sonnet-4-20250514",
        "encoding_version": "1.0.0"
    }
)
```

### `query_memory`

Semantic search across all memories with context-aware ranking.

**Parameters:**
- `query` (required): Natural language search query (must not be blank)
- `query_context` (required): Why the search is being performed
- `k` (optional): Number of primary results (default: 3, maximum: 20)
- `include_links` (optional): Include linked memories (default: true)
- `max_links_per_primary` (optional): Linked memories per primary result (default: 5)
- `importance_threshold` (optional): Minimum importance score
- `project_ids` (optional): Project IDs to search
- `strict_project_filter` (optional): Apply the project filter to linked memories

**Returns:**
- List of memories ranked by semantic relevance
- Each memory includes its 1-hop graph connections
- `scores`: One `MemoryScore` per primary memory (`memory_id`, `similarity`, `rerank_score`), aligned by index with `primary_memories`. Primaries are importance-sorted after retrieval, so list position is not similarity rank.

**Example:**
```python
results = execute_forgetful_tool(
    "query_memory",
    {
        "query": "how do we handle authentication",
        "query_context": "Implementing authentication for the API",
        "project_ids": [12],
        "importance_threshold": 7,
        "k": 5
    }
)
# Returns: {"primary_memories": [...], "linked_memories": [...], ...}
```

### `get_memory`

Retrieve complete memory details by ID.

**Parameters:**
- `memory_id` (required): Memory ID

**Returns:**
- Complete memory object with all fields and relationships

**Example:**
```python
memory = execute_forgetful_tool("get_memory", {"memory_id": 156})
```

### `update_memory`

Update existing memory fields (PATCH semantics - only updates provided fields).

**Parameters:**
- `memory_id` (required): Memory ID
- `title` (optional): Updated title (must not be blank)
- `content` (optional): Updated content (must not be blank)
- `importance` (optional): Updated importance score
- `context` (optional): Updated context (must not be blank)
- `keywords` (optional): Updated keywords (each max 100 chars)
- `tags` (optional): Updated tags (each max 100 chars)

**Provenance Tracking (optional):**
- `source_repo` (optional): Repository source (e.g., 'owner/repo')
- `source_files` (optional): List of file paths
- `source_url` (optional): URL to source material
- `confidence` (optional): Confidence score (0.0-1.0)
- `encoding_agent` (optional): Agent/process identifier
- `encoding_version` (optional): Version of encoding process

**Returns:**
- Updated memory object

**Example:**
```python
execute_forgetful_tool(
    "update_memory",
    {
        "memory_id": 156,
        "importance": 9,  # Increased importance after realizing how critical this is
        "tags": ["api", "security", "performance", "production"]
    }
)
```

**Example - Adding provenance after creation:**
```python
# Add provenance to an existing memory
execute_forgetful_tool(
    "update_memory",
    {
        "memory_id": 156,
        "source_repo": "company/api-gateway",
        "confidence": 0.95,
        "encoding_agent": "manual-review"
    }
)
```

### `link_memories`

Manually create bidirectional links between memories.

**Parameters:**
- `memory_id` (required, int): Source memory ID (aliases: `source_id`, `from_id`, `id`)
- `related_ids` (required, List[int] or int): Target memory IDs (aliases: `target_id`, `target_ids`, `related_id`, `linked_ids`)
- `memory_ids` (optional, List[int]): Pair/list of memory IDs `[src, target1, target2, ...]`

**Returns:**
- Dict with `linked_memory_ids` (list of target IDs newly linked). Target IDs that do not exist, belong to another user, or are already linked are skipped.

**Examples:**
```python
# Standard syntax
execute_forgetful_tool(
    "link_memories",
    {
        "memory_id": 156,  # Rate limiting decision
        "related_ids": [201]  # Redis caching strategy
    }
)

# Alias syntax with source_id and target_id
execute_forgetful_tool(
    "link_memories",
    {
        "source_id": 156,
        "target_id": 201
    }
)

# Pair list syntax
execute_forgetful_tool(
    "link_memories",
    {
        "memory_ids": [156, 201]
    }
)
```

### `unlink_memories`

Remove bidirectional link(s) between memories.

**Parameters:**
- `source_id` (required, int): Source memory ID (aliases: `memory_id`, `from_id`, `id`)
- `target_id` (required, int or List[int]): Target memory ID(s) to unlink (aliases: `target_ids`, `related_id`, `related_ids`, `linked_ids`)
- `memory_ids` (optional, List[int]): List of memory IDs `[src, target1, target2, ...]`

**Returns:**
- Dict with `success` boolean (True if all requested links were removed)

**Examples:**
```python
execute_forgetful_tool(
    "unlink_memories",
    {
        "source_id": 156,
        "target_id": 201
    }
)

execute_forgetful_tool(
    "unlink_memories",
    {
        "memory_id": 156,
        "related_ids": [201, 202]
    }
)

execute_forgetful_tool(
    "unlink_memories",
    {
        "memory_ids": [156, 201, 202]
    }
)
```

### `mark_memory_obsolete`

Soft delete a memory with audit trail and supersession tracking.

**Parameters:**
- `memory_id` (required): Memory ID to mark obsolete
- `reason` (required): Reason for obsolescence
- `superseded_by` (optional): ID of replacement memory (must be an existing memory other than `memory_id`)

**Returns:**
- Updated memory with obsolete flag

**Errors:** fails if the memory is already obsolete (use `unobsolete_memory` first; the original reason and timestamp are kept), if `superseded_by` equals `memory_id`, or if either memory is not found.

**Example:**
```python
execute_forgetful_tool(
    "mark_memory_obsolete",
    {
        "memory_id": 78,  # Old "Docker Swarm deployment" memory
        "reason": "Migrated to Kubernetes",
        "superseded_by": 312  # New K8s memory
    }
)
```

### `unobsolete_memory`

Restore a memory previously marked obsolete (undo `mark_memory_obsolete`).

**Parameters:**
- `memory_id` (required): ID of the obsolete memory to restore

**Returns:**
- Boolean success flag

**Example:**
```python
execute_forgetful_tool(
    "unobsolete_memory",
    {"memory_id": 78},
)
```

### `get_recent_memories`

Retrieve most recent memories sorted by creation timestamp.

**Parameters:**
- `limit` (optional): Max memories to return (default: 10)
- `project_ids` (optional): Scope to specific projects

**Returns:**
- An object (not a bare list) with two keys:
  - `memories`: list of recent memories, sorted by `created_at` DESC (newest first)
  - `total_count`: total number of matching memories
- Each memory's `linked_memory_ids` includes both directions of links (same set as `get_memory`).

**Example:**
```python
result = execute_forgetful_tool(
    "get_recent_memories",
    {"limit": 10, "project_ids": [12]}
)
recent = result["memories"]     # the list of memories
total = result["total_count"]   # how many matched in total
```

> **Behavior change (PR #49) — the return shape is now an envelope.**
>
> This tool used to return a bare JSON array of memories. It now returns an object:
> `{"memories": [...], "total_count": N}`.
>
> **Why:** MCP structured content requires an object root. A bare `[]` carries no
> structured content over the wire, so an empty result collapsed to `null` for remote
> consumers and could not be told apart from "no result". Wrapping the list in an object
> lets empty results survive the wire and matches the `list_*` tool family.
>
> **Migration:** read `result["memories"]` instead of indexing the bare result, and read
> `result["total_count"]` if you need the total. For example, replace `for m in result:`
> with `for m in result["memories"]:`. The REST endpoint is unaffected — this change is
> only in the MCP tool surface.

---

## Project Tools

Organize memories by project context.

### Project Types
- `personal`, `work`, `learning`, `development`, `infrastructure`
- `template`, `product`, `marketing`, `finance`, `documentation`
- `development-environment`, `third-party-library`, `open-source`

### Project Statuses
- `active`, `archived`, `completed`

### `create_project`

Create a new project for organizing knowledge.

**Parameters:**
- `name` (required): Project name
- `project_type` (optional): Project type (see list above)
- `description` (optional): Project description
- `status` (optional): Project status (default: `active`)
- `repository_url` (optional): Git repository URL
- `metadata` (optional): Additional JSON metadata
- `last_encoding_point` (optional): Opaque caller-managed encoding checkpoint (e.g. a Git commit SHA). The server never infers or advances it; the encoding client updates it only after a successful run.

**Returns:**
- Created project with `project_id`

**Example:**
```python
project = execute_forgetful_tool(
    "create_project",
    {
        "name": "E-Commerce Platform Redesign",
        "project_type": "work",
        "description": "Complete redesign of checkout and payment flows",
        "status": "active",
        "repository_url": "https://github.com/company/ecommerce-v2"
    }
)
# Returns: {"project_id": 22, "name": "E-Commerce Platform Redesign", ...}
```

### `list_projects`

List projects with optional filtering.

**Parameters:**
- `project_type` (optional): Filter by project type
- `status` (optional): Filter by status
- `repository_url` (optional): Filter by repository

**Returns:**
- List of matching projects

**Example:**
```python
# Get all active work projects
active_work = execute_forgetful_tool(
    "list_projects",
    {"project_type": "work", "status": "active"}
)
```

### `get_project`

Retrieve complete project details by ID.

**Parameters:**
- `project_id` (required): Project ID

**Returns:**
- Complete project object

**Errors:**
- Raises an error (not `null`) when `project_id` does not exist or is not visible to the caller

**Example:**
```python
project = execute_forgetful_tool("get_project", {"project_id": 22})
```

### `update_project`

Update project metadata (PATCH semantics).

**Parameters:**
- `project_id` (required): Project ID
- `name` (optional): Updated name
- `description` (optional): Updated description
- `status` (optional): Updated status
- `repository_url` (optional): Updated repository URL
- `metadata` (optional): Updated metadata
- `last_encoding_point` (optional): Opaque caller-managed encoding checkpoint (e.g. a Git commit SHA). The server never infers or advances it; the encoding client updates it only after a successful run. Unchanged if omitted; set to empty string to clear.

**Returns:**
- Updated project object

**Example:**
```python
# Mark project as completed
execute_forgetful_tool(
    "update_project",
    {
        "project_id": 22,
        "status": "completed",
        "metadata": {"completion_date": "2025-01-15", "outcome": "shipped to production"}
    }
)
```

### `delete_project`

Delete project while preserving linked memories.

**Parameters:**
- `project_id` (required): Project ID

**Returns:**
- Confirmation of deletion

**Example:**
```python
execute_forgetful_tool("delete_project", {"project_id": 22})
```

---

## Cross-Category Workflows

Real-world scenarios demonstrating how tools work together.

### Scenario 1: Capturing a Complete Feature

**Context:** You've implemented a new authentication system and want to capture all knowledge.

```python
# 1. Create project
project = execute_forgetful_tool(
    "create_project",
    {
        "name": "Authentication System V2",
        "project_type": "development",
        "status": "active"
    }
)
project_id = project["id"]

# 2. Store the key decision as an atomic memory
decision_memory = execute_forgetful_tool(
    "create_memory",
    {
        "title": "Auth strategy: OAuth2 for third-party + JWT for sessions",
        "content": "Use OAuth2 for social login and JWT for internal sessions.",
        "context": "Capturing the authentication architecture decision",
        "keywords": ["authentication", "oauth2", "jwt"],
        "tags": ["authentication", "decision"],
        "importance": 10,
        "project_ids": [project_id]
    }
)

# 3. Store the supporting detail as a second atomic memory and link the two
middleware_memory = execute_forgetful_tool(
    "create_memory",
    {
        "title": "JWT middleware: validate the bearer token in a FastAPI dependency",
        "content": "A FastAPI dependency reads the Authorization header, decodes the JWT with the shared secret (HS256) and answers 401 on a JWTError.",
        "context": "Implementation detail of the authentication strategy",
        "keywords": ["authentication", "jwt", "middleware", "fastapi"],
        "tags": ["authentication", "jwt", "middleware"],
        "importance": 8,
        "project_ids": [project_id]
    }
)
execute_forgetful_tool(
    "link_memories",
    {"memory_id": decision_memory["id"], "related_ids": [middleware_memory["id"]]}
)

# 4. Later, query everything about auth
results = execute_forgetful_tool(
    "query_memory",
    {
        "query": "authentication implementation",
        "query_context": "Reviewing the authentication implementation",
        "project_ids": [project_id]
    }
)
# Returns decision_memory + auto-linked memories + linked memories
```

### Scenario 2: New Team Member Onboarding

**Context:** A new engineer joins, you want to capture their focus areas and link relevant knowledge.

```python
# 1. Create onboarding memory
onboarding_memory = execute_forgetful_tool(
    "create_memory",
    {
        "title": "Jordan Taylor joined - Payments team focus areas",
        "content": "Jordan will focus on payment integrations and PCI compliance.",
        "context": "New hire onboarding for the payments team",
        "keywords": ["jordan", "payments", "stripe", "pci"],
        "tags": ["team", "onboarding", "payments"],
        "importance": 7
    }
)

# 2. Query existing payment system memories
payment_memories = execute_forgetful_tool(
    "query_memory",
    {
        "query": "payment gateway stripe paypal",
        "query_context": "Finding onboarding material for the payments team",
        "k": 5
    }
)

# 3. Link the relevant ones to the onboarding memory
execute_forgetful_tool(
    "link_memories",
    {
        "memory_id": onboarding_memory["id"],
        "related_ids": [memory["id"] for memory in payment_memories["primary_memories"]]
    }
)
```

### Scenario 3: Infrastructure Incident Capture

**Context:** Redis server failed, you resolved it and want to record it for future reference.

```python
# 1. Create incident memory
incident = execute_forgetful_tool(
    "create_memory",
    {
        "title": "Redis failover incident - memory exhaustion",
        "content": "Cache Server 01 ran out of memory due to unbounded key growth. Implemented maxmemory-policy=allkeys-lru and set maxmemory=4gb. Also added monitoring alerts at 80% memory usage.",
        "importance": 9,
        "tags": ["incident", "redis", "infrastructure", "production"],
        "context": "Production incident on 2025-01-18, resolved in 45 minutes",
        "keywords": ["redis", "memory", "failover", "monitoring"]
    }
)

# 2. Record the follow-up as a memory
followup = execute_forgetful_tool(
    "create_memory",
    {
        "title": "Implemented Redis memory monitoring",
        "content": "A check compares used_memory with maxmemory every minute and alerts at 80% usage to prevent future incidents.",
        "context": "Follow-up action from the Redis memory exhaustion incident",
        "keywords": ["redis", "memory", "monitoring", "alert"],
        "tags": ["monitoring", "prevention", "redis"],
        "importance": 8
    }
)

# 3. Link the follow-up to the incident memory
execute_forgetful_tool(
    "link_memories",
    {"memory_id": incident["id"], "related_ids": [followup["id"]]}
)
```

### Scenario 4: Research and Decision Making

**Context:** Researching database options, recording findings, and making a decision.

```python
# 1. Create research project
research_project = execute_forgetful_tool(
    "create_project",
    {
        "name": "Database Technology Evaluation 2025",
        "project_type": "learning",
        "status": "active"
    }
)
project_id = research_project["id"]

# 2. Capture atomic insights as memories
insight1 = execute_forgetful_tool(
    "create_memory",
    {
        "title": "pgvector: Best for existing PostgreSQL setups",
        "content": "pgvector avoids a separate vector database when PostgreSQL is in use.",
        "context": "Capturing a conclusion from the vector database comparison",
        "keywords": ["pgvector", "postgresql", "vector-database"],
        "tags": ["database", "pgvector", "vectors"],
        "importance": 8,
        "project_ids": [project_id]
    }
)

insight2 = execute_forgetful_tool(
    "create_memory",
    {
        "title": "Qdrant: Best performance for large-scale vector search",
        "content": "Qdrant performs well at large scale but requires a separate service.",
        "context": "Capturing a conclusion from the vector database comparison",
        "keywords": ["qdrant", "vector-database", "performance"],
        "tags": ["database", "qdrant", "vectors"],
        "importance": 8,
        "project_ids": [project_id]
    }
)

# 3. Make decision and create decision memory
decision = execute_forgetful_tool(
    "create_memory",
    {
        "title": "Decision: pgvector for Forgetful project",
        "content": "Selected pgvector because Forgetful already uses PostgreSQL.",
        "context": "Final decision from the vector database evaluation",
        "keywords": ["pgvector", "postgresql", "database-decision"],
        "tags": ["decision", "database", "pgvector", "forgetful"],
        "importance": 10,
        "project_ids": [project_id]
    }
)

# 4. Manually link related insights to decision
execute_forgetful_tool(
    "link_memories",
    {"memory_id": decision["id"], "related_ids": [insight1["id"]]}
)

execute_forgetful_tool(
    "link_memories",
    {"memory_id": decision["id"], "related_ids": [insight2["id"]]}
)

# 5. Mark project as completed
execute_forgetful_tool(
    "update_project",
    {
        "project_id": project_id,
        "status": "completed",
        "notes": "Selected pgvector; evaluation completed 2025-01-15"
    }
)
```

---

## Best Practices

### Memory Creation
- **One concept per memory** - Follow atomic memory principle
- **Title it first** - If you can't easily title it, break it down further
- **High importance for reusable knowledge** - Use 8-10 for architectural decisions and patterns
- **Always provide context** - Future you will thank past you
- **Tag consistently** - Develop a tagging taxonomy for your knowledge domain

### Project Organization
- **Scope queries to projects** - Dramatically improves search relevance
- **Use project types consistently** - Helps with filtering and organization
- **Archive completed projects** - Don't delete - preserve knowledge with context
- **Link related projects** - Create memories that reference multiple projects

### Knowledge Graph
- **Build links as you learn** - Don't wait to build complete graphs
- **Capture context liberally** - Record temporal and contextual information in the `context` field
- **Link related memories** - Creates rich context for queries

### Long-form Knowledge
- **Split detailed content (>400 words) into atomic memories** - ADRs, research and specifications become several memories, one concept each
- **Link the atoms to each other** - Use `link_memories` to preserve the connection between related pieces
- **Keep code snippets short** - A memory holds ~2000 characters, so store the idea and point at the source file with `source_files`

### Search & Retrieval
- **Query early and often** - Check existing knowledge before creating duplicates
- **Use natural language** - Semantic search works better than keyword matching
- **Filter by project for focus** - Especially in multi-project environments
- **Check auto-linked memories** - Often find related context you didn't know existed

---

## Token Budget & Performance

Forgetful protects your LLM context window with configurable token budgets:

- **Default: 8,000 tokens** per query result
- **Max 20 memories** returned per query
- **Primary result order**: Importance (high first); ties keep search order, even without truncation
- **Graceful truncation**: If over budget, lower-priority memories excluded

Configure via environment variables:
```bash
MEMORY_TOKEN_BUDGET=8000
MEMORY_MAX_MEMORIES=20
```

---

## Additional Resources

- [Configuration Guide](configuration.md) - All environment variables
- [Connectivity Guide](connectivity_guide.md) - MCP client setup
- [Search Documentation](search.md) - Embedding pipeline details
- [MCP Protocol](https://modelcontextprotocol.io/) - MCP specification

---

**Last Updated:** 2025-01-22
**Forgetful Version:** 0.1.x
