# Superseded

The Copilot CLI-specific skill copies that lived here have been retired. Skills are now
maintained once, in the canonical [`skills/`](../../../skills/) directory at the repository
root — single-file and surface-neutral (MCP and CLI).

| Was here | Now covered by |
|----------|----------------|
| `using-axmem-memory` | `axmem-remember`, `axmem-recall` |
| `curating-memories` | folded into `axmem-remember` |
| `exploring-knowledge-graph` | `axmem-explore` |

See [`skills/README.md`](../../../skills/README.md) for the full catalog. Install for
Copilot CLI by copying from the canonical set:

```bash
# Global installation (all projects)
cp -r skills/axmem-* ~/.copilot/skills/

# Or repository-specific
mkdir -p .github/skills
cp -r skills/axmem-* .github/skills/
```
