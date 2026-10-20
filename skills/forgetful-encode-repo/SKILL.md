---
name: forgetful-encode-repo
description: >-
  Encode a repository into the Forgetful knowledge base — bootstrap the project and its
  memories from the codebase itself. Use when bringing a new repo
  under Forgetful or refreshing a stale encoding. Re-encoding is an update pass:
  query-before-create makes it supersession, not duplication.
license: MIT
disable-model-invocation: true
tags: [bootstrap, encoding, repository, knowledge-base]
allowed-tools:
  - mcp__forgetful__discover_forgetful_tools
  - mcp__forgetful__how_to_use_forgetful_tool
  - mcp__forgetful__execute_forgetful_tool
  - Bash(forgetful:*)
---

# Encoding a repository

The goal is a knowledge base a future session can lean on: the decisions, conventions and long-form
understanding as atomic memories, everything provenance-stamped back to the source. Encode
twice and the second pass updates — never duplicates.

## Invoking operations

Operations are named by registry name (`query_memory`, `create_memory`, ...). Invoke via
whichever surface this agent has:

- **MCP**: `execute_forgetful_tool(tool_name="create_memory", arguments={...})`
- **CLI**: `forgetful call create_memory --args '{"title": "..."}' --json`

Get any operation's schema at runtime: `how_to_use_forgetful_tool` (MCP) or
`forgetful tools info <operation>` (CLI) — schemas are deliberately not repeated here.

## Step 1 — Resolve the project scope

`git remote get-url origin` → `owner/repo`, used as the `project` label of every memory in this
run. A project is only a label, so there is nothing to create: `get_recent_memories` with that
`project` shows what the scope already holds. Memories present means this run is a refresh.

Done when: the project label is fixed and the run knows whether it is a first encode or a refresh.

## Step 2 — Survey the sources

Read what the repo says about itself before reading code: README, docs/, contributor and
agent guides, manifests (dependencies, entry points, scripts), CI and deploy configuration.
Note the current commit — every write in this run cites it via provenance.

Done when: there is a source list of what will be encoded, ordered by signal.

## Step 3 — Store the knowledge

Atomic memories, per `forgetful-remember` (its routing, atomicity test, and importance rubric
govern every write here):

- **Long-form understanding** (architecture overviews, subsystem guides, design analyses)
  is split into several atomic memories, one per decision, fact or pattern, tied together
  with `link_memories`.
- **Decisions, conventions, patterns, constraints** are memories of their own, stamped with
  `source_repo` and `source_files`, and linked to the memories they relate to with
  `link_memories`.

On a refresh, query first per source area: update what drifted, mark obsolete what the code
contradicts, create only what is new.

Done when: each surveyed source area has produced its memories,
or explicitly had nothing worth encoding.

## Step 4 — Report coverage

Close with a coverage report, in place of "done": project ID,
memories created/updated/obsoleted, source areas skipped and why, and
the areas where knowledge is thin. Note repo-level gaps the survey turned up too — missing
docs, absent CI, and the like — not only gaps in the encoding itself. Deliver the report in
the final response to the user; it is not written anywhere in Forgetful. The report is the
encode's acceptance surface — a reader should be able to spot a gap from it alone.

Done when: the report is delivered and honest about gaps.
