---
name: forgetful-explore
description: >-
  Explore the Forgetful knowledge graph when flat search isn't enough — cross-project
  investigations, "what do we know about X", tracing how decisions connect. Use when recall
  returns fragments that trail across domains. Walks memories, their links, and project
  scopes into one synthesized picture.
license: MIT
tags: [knowledge-graph, traversal, links, investigation]
allowed-tools:
  - mcp__forgetful__discover_forgetful_tools
  - mcp__forgetful__how_to_use_forgetful_tool
  - mcp__forgetful__execute_forgetful_tool
  - Bash(forgetful:*)
---

# Exploring the knowledge graph

A flat query answers "what matches"; exploration answers "how it connects". The output is a
synthesized picture that states the path taken — never a raw dump of hops.

## Invoking operations

Operations are named by registry name (`query_memory`, `get_memory`, ...).
Invoke via whichever surface this agent has:

- **MCP**: `execute_forgetful_tool(tool_name="query_memory", arguments={...})`
- **CLI**: `forgetful call query_memory --args '{"query": "...", "query_context": "..."}' --json`

Get any operation's schema at runtime: `how_to_use_forgetful_tool` (MCP) or
`forgetful tools info <operation>` (CLI) — schemas are deliberately not repeated here.

## Choose a depth first

- **Shallow** (phases 1–2): confirm or refresh something specific.
- **Medium** (phases 1–3): understand a topic and its immediate neighbourhood.
- **Deep** (all phases): cross-project investigation or "everything we know about X".

Track visited memory IDs throughout — a revisited ID means that branch is done.

## Phase 1 — Semantic entry

Broad `query_memory` on the topic (with `query_context` stating the investigation's intent).
The goal is entry points, so favour breadth over precision here — raise `k` above its
default of 3 (max 20) rather than settle for a narrow first pass.

Done when: a handful of strong entry memories are identified.

## Phase 2 — Expand the strongest hits

`get_memory` on each entry point for full content and linked memory IDs; follow the links
that bear on the question.

Done when: each entry point's local cluster is understood.

## Phase 3 — Walk the links

Follow the linked memory IDs of each expanded memory one hop outward with `get_memory`,
keeping only the links that answer the question. Skip any ID already in the visited set, so
cycles end themselves.

Done when: the connections between the memories are mapped and every branch is either
followed or ruled out.

## Phase 4 — Widen across projects

`list_projects`, then `query_memory` with `project_ids` for each project that could hold
related knowledge — this surfaces memories the unscoped entry query ranked too low. Expand
new hits the same way as in phases 2–3.

Done when: every relevant project has been queried or ruled out.

## Synthesize

Report the picture, not the walk: what is known, how it connects, where knowledge is thin.
Cite memory IDs so the user can jump in, and state the traversal path in one line
(e.g. "query → 3 memories → 2 linked decisions → 1 memory from the billing project").

Done when: the user gets a connected answer with cited IDs and visible gaps.
