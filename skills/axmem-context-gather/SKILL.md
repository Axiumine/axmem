---
name: axmem-context-gather
description: >-
  Gather deep context before planning or implementing — one pass that turns a task
  description into a cited context pack: relevant decisions, patterns, constraints, code
  pointers, and explicit gaps. Runs recall from several angles and explores the
  graph around the strongest hits.
license: AGPL-3.0-only
disable-model-invocation: true
tags: [context, retrieval, workflow, pre-work]
allowed-tools:
  - mcp__axmem__discover_axmem_tools
  - mcp__axmem__how_to_use_axmem_tool
  - mcp__axmem__execute_axmem_tool
  - Bash(axmem:*)
---

# Gathering context before work

A single query answers a question; a context gather prepares a task. The output is a pack
the work can lean on, with every claim cited by memory ID and every dry angle stated —
silence is never ambiguous.

## Invoking operations

Operations are named by registry name (`query_memory`, `get_memory`, ...). Invoke via
whichever surface this agent has:

- **MCP**: `execute_axmem_tool(tool_name="query_memory", arguments={...})`
- **CLI**: `axmem call query_memory --args '{"query": "..."}' --json`

Get any operation's schema at runtime: `how_to_use_axmem_tool` (MCP) or
`axmem tools info <operation>` (CLI) — schemas are deliberately not repeated here.

Delegating this workflow to a subagent: hand it the CLI flavor — `Bash` travels into
subagents, MCP tool access does not. Run MCP-flavored gathers in the main conversation.

## Step 1 — Derive the query angles

From the task description, write 3–5 distinct angles: the feature area, the technology or
library, exact error text or identifiers, the architectural layer, adjacent domains that
share patterns. One angle is a spot-check; several are coverage.

Done when: the angles are listed before any query runs.

## Step 2 — Recall per angle

`query_memory` per angle — `query` and `query_context` are both required, and
`query_context` should carry the task's intent (per `axmem-recall`: reads cross-project
by default, identifiers verbatim, `truncated` handled). Track which angles hit and which
came up dry.

Done when: every angle has run and its outcome is recorded.

## Step 3 — Explore around the strongest hits

Where hits cluster into a topic, walk the graph (per `axmem-explore`, medium depth):
expand the memories and follow their links outward.

Done when: the strongest hits have their neighbourhood mapped, not just their own text.

## Step 4 — Synthesize the pack

Assemble, in order of usefulness to the task:

- **Decisions** that constrain the work (cited by memory ID)
- **Patterns and preferences** that shape how it should be done
- **Constraints** — things ruled out, with the reason
- **Code pointers** — source references worth opening
- **Gaps** — every angle that came up dry, stated as "no existing knowledge about X"

Done when: the pack cites its sources, states its gaps, and the task could start from it
without re-querying.
