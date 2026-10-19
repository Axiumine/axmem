# Forgetful Memory System

## When to Create Memories
Create for: decisions, preferences, patterns, technical solutions, project context.
Skip: transient info, common knowledge, single-use throwaway.

## Atomic Memory Principle
ONE concept per memory (~300 words max). For long-form content, split it into several atomic memories
and link them with `link_memories`.

## Entities vs Memories
- **Entities**: Concrete things (people, orgs, teams, devices) - use `create_entity`
- **Memories**: Abstract knowledge (decisions, patterns, learnings)
- **Link to memories**: `link_entity_to_memory` connects entities to relevant knowledge
- **Link to projects**: `link_entity_to_project` groups entities by project context
- **Relationships**: `create_entity_relationship` for "works_for", "owns", "manages"

## Best Practices
- **Query first**: Check if similar memory exists before creating
- **Use projects**: Scope searches with `project_ids` for relevant retrieval
- **Importance**: 9-10=foundational, 7-8=useful patterns, <6=discourage
- **Auto-linking**: Similar memories link automatically (≥0.7 similarity by default)
