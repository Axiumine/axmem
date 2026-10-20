---
description: List recent memories from Forgetful
---
# List Recent Memories

Show the most recently created memories from Forgetful.

## Your Task

Retrieve recent memories using `execute_forgetful_tool("get_recent_memories", {...})`.

**Arguments**: $ARGUMENTS

## Parameters

Parse the arguments for:
- **Number**: If user specifies a count (e.g., "10", "last 5"), use that as `limit`
- **Project**: If user mentions a project name, use it as the `project` scope label

Default: `{"limit": 10}`

## Response Format

Present memories in a clean, scannable format:

```
Recent Memories (showing X of Y):

1. [Title] (Importance: X, Created: date)
   Tags: [tags]

2. [Title] (Importance: X, Created: date)
   Tags: [tags]

...
```

## Optional Enhancements

If the user asks for more detail on any memory, use `get_memory` to retrieve full content.

To filter by project, pass the project scope label as `project`:
```
execute_forgetful_tool("get_recent_memories", {"limit": 10, "project": "<project scope>"})
```
Infer the label from context or ask the user; a project is just a label, so there is no list to look it up in.

## Examples

**Basic usage:**
```
/memory-list
```
Returns last 10 memories across all project scopes.

**With count:**
```
/memory-list 5
```
Returns last 5 memories.

**With project filter:**
```
/memory-list forgetful project
```
Returns recent memories in the "forgetful" project scope.
