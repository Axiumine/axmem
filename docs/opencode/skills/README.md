# Superseded

The OpenCode-specific skill copies that lived here have been retired. Skills are now
maintained once, in the canonical [`skills/`](../../../skills/) directory at the repository
root — single-file, surface-neutral (MCP and CLI), and importable into Axmem itself.

| Was here | Now covered by |
|----------|----------------|
| `using-axmem-memory` | `axmem-remember`, `axmem-recall` |
| `curating-memories` | folded into `axmem-remember` |
| `exploring-knowledge-graph` | `axmem-explore` |

See [`skills/README.md`](../../../skills/README.md) for the full catalog. Install for
OpenCode by copying from the canonical set:

```bash
# Global installation
cp -r skills/axmem-* ~/.config/opencode/skill/

# Or project-specific
cp -r skills/axmem-* .opencode/skill/
```
