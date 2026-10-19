---
name: forgetful-encode-repo
description: >-
  Encode a repository into the Forgetful knowledge base — bootstrap the project, its
  entities, memories, and documents from the codebase itself. Use when bringing a new repo
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

The goal is a knowledge base a future session can lean on: the system modelled as entities,
the decisions and conventions as atomic memories, the long-form understanding as documents,
everything provenance-stamped back to the source. Encode twice and the second pass updates
— never duplicates.

## Invoking operations

Operations are named by registry name (`create_project`, `create_entity`, ...). Invoke via
whichever surface this agent has:

- **MCP**: `execute_forgetful_tool(tool_name="create_project", arguments={...})`
- **CLI**: `forgetful call create_project --args '{"name": "..."}' --json`

Get any operation's schema at runtime: `how_to_use_forgetful_tool` (MCP) or
`forgetful tools info <operation>` (CLI) — schemas are deliberately not repeated here.

## Step 1 — Resolve the project

`git remote get-url origin` → `owner/repo` → `list_projects` with `repo_name`. Existing
project means this run is a refresh; otherwise `create_project` with the repo name and a
description of what the codebase is for.

Done when: a project_id exists and the run knows whether it is a first encode or a refresh.

## Step 2 — Survey the sources

Read what the repo says about itself before reading code: README, docs/, contributor and
agent guides, manifests (dependencies, entry points, scripts), CI and deploy configuration.
Note the current commit — every write in this run cites it via provenance.

Done when: there is a source list of what will be encoded, ordered by signal.

## Step 3 — Model the system as entities

Per `forgetful-entities`: the system itself and its key components
(services, packages, databases, external dependencies that matter) as `entity_type:
"System"`, and their structure as relationships — `part_of` for composition, `depends_on`
for coupling. Dedupe against existing entities on a refresh.

Done when: the architecture is walkable as a graph, components to system, dependencies out.

## Step 4 — Store the knowledge

Two layers, per `forgetful-remember` (its routing, atomicity test, and importance rubric
govern every write here):

- **Documents** for long-form understanding: architecture overviews, subsystem guides,
  design analyses.
- **Atomic memories** as the entry points: decisions, conventions, patterns, constraints —
  each linked to its document via `document_ids`, stamped with `source_repo` and
  `source_files`, and linked to the entities it concerns via a separate
  `link_entity_to_memory` call (not something `create_memory` does for you).

On a refresh, query first per source area: update what drifted, mark obsolete what the code
contradicts, create only what is new.

Done when: each surveyed source area has produced its documents and entry-point memories,
or explicitly had nothing worth encoding.

## Step 5 — Report coverage

Close with a coverage report, in place of "done": project ID, entities created/updated,
memories created/updated/obsoleted, documents written, source areas skipped and why, and
the areas where knowledge is thin. Note repo-level gaps the survey turned up too — missing
docs, absent CI, and the like — not only gaps in the encoding itself. Deliver the report in
the final response to the user; it is not written anywhere in Forgetful. The report is the
encode's acceptance surface — a reader should be able to spot a gap from it alone.

Done when: the report is delivered and honest about gaps.
