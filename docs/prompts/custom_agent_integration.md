# Forgetful Memory System

You have access to Forgetful, a semantic memory system that provides persistent knowledge storage across conversations and sessions.

## Overview

Forgetful is an MCP (Model Context Protocol) server that gives AI agents persistent memory. It stores knowledge as atomic notes using Zettelkasten principles, automatically builds knowledge graphs through semantic linking, and retrieves relevant context through natural language queries.

**Key capability**: Forgetful exposes only 3 meta-tools that provide access to 17 internal tools. This preserves your context window (~500 tokens vs ~15-20K if all tools were exposed directly).

---

## Core Principles

### Atomic Memory
Each memory contains ONE concept. If you can't easily title something in 5-50 words, break it down further.

| Field | Limit | Purpose |
|-------|-------|---------|
| Title | 200 chars | Short, searchable phrase |
| Content | 2000 chars | Single concept (~300-400 words) |
| Context | 500 chars | Why this matters |
| Keywords | 10 max | Semantic clustering |
| Tags | 10 max | Categorization |

### Importance Scoring
Use the 1-10 scale consistently:
- **9-10**: Foundational decisions, personal facts, architectural patterns
- **8-9**: Critical solutions, major decisions
- **7-8**: Useful patterns, preferences, tool choices
- **6-7**: Project milestones, specific solutions
- **<6**: Minor context (avoid creating these)

### Auto-Linking
When you create a memory, Forgetful automatically:
1. Generates an embedding vector for the content
2. Finds semantically similar memories (≥0.7 cosine similarity by default)
3. Creates bidirectional links (default: 3 connections)
4. Returns `auto_linked_to` list showing what was connected

### Token Budget
Queries return at most 20 memories, capped at ~8K tokens by default. Primary results are
sorted by importance (high first); ties keep search order. This applies even when nothing
is truncated.

---

## When to Use Forgetful (Trigger Patterns)

Adapt these patterns to your agent's domain.

### Query Memory When...

**User references the past:**
- "Remember when..." / "Last time we talked about..."
- "What did we decide about..."
- "You know how I like to..."

**Context would help:**
- Starting a new session with returning user
- Topic relates to user's known interests/work
- Question might have been answered before

### Create Memory When...

**User shares important information:**
- Personal facts (family, preferences, important dates)
- Decisions with reasoning
- Preferences and how they like things done
- Goals, plans, or ongoing projects

**Knowledge is reusable:**
- Solutions to problems they may encounter again
- Patterns in how they work or think
- Relationships between concepts

### Skip Forgetful When...

- Information is momentary (today's weather, current time)
- Knowledge is universally available
- Casual/social exchange with no lasting value
- Would score below importance 6

---

## The Meta-Tools Pattern

Forgetful uses 3 gateway tools to access all functionality:

### 1. `discover_forgetful_tools(category?)`
List available tools, optionally filtered by category.

```python
# See all tool categories
discover_forgetful_tools()

# See only project tools
discover_forgetful_tools(category="project")
```

Categories: `user`, `memory`, `project`

### 2. `how_to_use_forgetful_tool(tool_name)`
Get detailed documentation for a specific tool including parameters and examples.

```python
how_to_use_forgetful_tool("create_memory")
```

### 3. `execute_forgetful_tool(tool_name, arguments)`
Execute any registered tool dynamically.

```python
execute_forgetful_tool("create_memory", {
    "title": "API design: REST over GraphQL",
    "content": "Chose REST for this project because...",
    "context": "Recording the API design decision",
    "keywords": ["api", "rest", "graphql"],
    "tags": ["api", "decision"],
    "importance": 8
})
```

**Pattern**: Discover → Learn → Execute. You can skip discovery once you know the tools.

---

## Two Building Blocks

Forgetful organizes knowledge across 2 types:

| Type | What it stores | When to use |
|------|---------------|-------------|
| **Memories** | Atomic concepts, decisions, patterns | ONE concept that's directly queryable |
| **Projects** | Organizational scope | Group related knowledge |

### Quick Decision Tree

```
Is this >300 words of detailed content?
  → Yes: Split it into several atomic MEMORIES and link them
  → No: Continue...

Is this ONE concept/decision/pattern?
  → Yes: Create MEMORY
  → No: Break it down, then create MEMORIES
```

---

## Essential Tool Reference

### Memory Tools

| Tool | Required Params | Purpose |
|------|-----------------|---------|
| `create_memory` | `title`, `content`, `context`, `keywords`, `tags`, `importance` | Store |
| `query_memory` | `query`, `query_context` | Semantic search |
| `get_memory` | `memory_id` | Retrieve by ID |
| `update_memory` | `memory_id` | Patch fields |
| `link_memories` | `memory_id`, `related_ids` | Manual linking |
| `mark_memory_obsolete` | `memory_id`, `reason` | Soft delete |
| `get_recent_memories` | (none) | Timeline view |

### Project Tools

| Tool | Required Params | Purpose |
|------|-----------------|---------|
| `create_project` | `name`, `description`, `project_type` | Create scope |
| `list_projects` | (none) | List all |
| `get_project` | `project_id` | Get details |
| `update_project` | `project_id` | Modify |
| `delete_project` | `project_id` | Remove (keeps memories) |

---

## Common Workflows

### 1. Query Before Create (Prevent Duplicates)

Always check if similar knowledge exists:

```python
# Before creating memory about auth
results = execute_forgetful_tool("query_memory", {
    "query": "authentication OAuth JWT",
    "query_context": "Checking for existing auth memories before creating",
    "k": 5
})

if similar_exists(results):
    # Update existing memory instead
    execute_forgetful_tool("update_memory", {...})
else:
    # Create new memory
    execute_forgetful_tool("create_memory", {...})
```

### 2. Project Discovery

Find the correct project before creating memories:

```python
# List projects, optionally filter by repo
projects = execute_forgetful_tool("list_projects", {
    "repo_name": "owner/repo-name"
})

project_id = projects["projects"][0]["id"] if projects["projects"] else None
```

### 3. Memory Creation Template

```python
execute_forgetful_tool("create_memory", {
    "title": "[Concise, searchable, <200 chars]",
    "content": "[ONE concept, ~300-400 words, max 2000 chars]",
    "importance": 8,  # 1-10 scale
    "context": "[Why this matters, max 500 chars]",
    "keywords": ["kw1", "kw2"],  # max 10
    "tags": ["tag1", "tag2"],  # max 10
    "project_ids": [PROJECT_ID]  # scope to project
})
```

### 4. Long Content Decomposition

For long content, split it into atomic memories and link them:

```python
# 1. Capture each key decision as an atomic memory
memory1 = execute_forgetful_tool("create_memory", {
    "title": "Architecture: Event-driven with Kafka",
    "content": "Selected event-driven architecture...",
    "context": "Extracted from ADR-003",
    "keywords": ["architecture", "kafka", "events"],
    "tags": ["architecture", "decision"],
    "importance": 10,
    "project_ids": [PROJECT_ID]
})

memory2 = execute_forgetful_tool("create_memory", {
    "title": "Tradeoff: Eventual consistency accepted",
    "content": "Team accepted eventual consistency...",
    "context": "Extracted from ADR-003",
    "keywords": ["eventual-consistency", "tradeoff"],
    "tags": ["architecture", "tradeoff"],
    "importance": 8,
    "project_ids": [PROJECT_ID]
})

# 2. Link the pieces to each other
execute_forgetful_tool("link_memories", {
    "memory_id": memory1["id"],
    "related_ids": [memory2["id"]]
})
```

### 5. Knowledge Graph Building

Build graphs incrementally as you learn. Auto-linking connects each new memory to its nearest
neighbours; add manual links for connections embeddings miss:

```python
# Find the earlier memory the new one builds on
matches = execute_forgetful_tool("query_memory", {
    "query": "Kafka event-driven architecture",
    "query_context": "Finding the decision this tradeoff follows from"
})

# Link the new memory to it
execute_forgetful_tool("link_memories", {
    "memory_id": memory["id"],
    "related_ids": [matches["primary_memories"][0]["id"]]
})
```

---

## Best Practices & Anti-Patterns

### DO

- **Query before create** - Check for existing memories to prevent duplicates
- **Use project scoping** - Filter queries with `project_ids` for relevance
- **Link liberally** - Connect related memories to each other
- **Mark obsolete, don't delete** - Preserve knowledge with audit trail
- **Build links incrementally** - Add graph edges as you learn

### DON'T

- **Create memories for transient info** - Current file paths, temporary errors
- **Assume project_id** - Always discover the correct project first
- **Store common knowledge** - Things readily available elsewhere
- **Exceed character limits** - Split into atomic pieces instead
- **Create low-importance memories (<6)** - Focus on reusable knowledge
- **Store entire documents as memories** - Split them into atomic memories

---

## Quick Reference

```python
# Essential patterns

# 1. Query memory
execute_forgetful_tool("query_memory", {
    "query": "search terms",
    "query_context": "why searching"
})

# 2. Create memory
execute_forgetful_tool("create_memory", {
    "title": "Short title",
    "content": "One concept",
    "context": "Why this matters",
    "keywords": ["search-term"],
    "tags": ["category"],
    "importance": 8
})

# 3. Link memories
execute_forgetful_tool("link_memories", {
    "memory_id": 42,
    "related_ids": [156]
})
```

---

*Last updated: 2026-01*
