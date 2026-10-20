# Axmem Skills

Canonical agent-facing skills for using Axmem. This directory is the single source of
truth — per-client packagings (Claude Code plugin, OpenCode, Copilot CLI) derive from these
files rather than maintaining their own copies.

## Design rules

- **Skills carry judgment, the product carries schemas.** No tool schema is ever embedded in
  a skill. Discovery is part of the product: `discover_axmem_tools` /
  `how_to_use_axmem_tool` on MCP, `axmem tools list` / `axmem tools info` on the
  CLI — both served fresh from the same registry at runtime.
- **Surface-neutral bodies.** Operations are referenced by registry name (`query_memory`,
  `create_memory`). Each skill opens with the same two-line invocation mapping for MCP and
  CLI. Only the two setup skills are surface-specific.
- **Single-file skills.** Every skill is one SKILL.md with no sibling files, so a skill
  can be copied into any client's skills directory as is.
- **Descriptions are the trigger.** They are the model-invocation trigger in Claude Code, so
  each one says what the skill does and when to reach for it.

## Catalog

| Skill | Invocation | Fires when |
|---|---|---|
| `axmem-remember` | model | Knowledge worth keeping appears; decides what is worth a memory |
| `axmem-recall` | model | Context needed: task start, "have we solved this before" |
| `axmem-explore` | model | Flat search isn't enough; the connected picture matters |
| `axmem-context-gather` | user | Deep pre-work sweep producing a cited context pack |
| `axmem-encode-repo` | user | Bootstrap or refresh a repository in the knowledge base |
| `axmem-cli-setup` | user | Connect a terminal/headless agent; operate a local server |
| `axmem-mcp-setup` | user | Wire an MCP client and verify the connection |
