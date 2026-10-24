# Nix Guide

Run and develop Axmem with [Nix flakes](https://nixos.wiki/wiki/Flakes).
`uv.lock` stays the single source of truth for exact dependency versions —
Nix builds the locked environment reproducibly.

## Prerequisites

- Nix with flakes enabled (`experimental-features = nix-command flakes` in
  `~/.config/nix/nix.conf`, or NixOS / nix-installer which set it up for you)
- Supported systems: `x86_64-linux`, `aarch64-linux` (Axmem runs its models on an NVIDIA GPU
  with CUDA 13 and TensorRT 11, which macOS does not have)

## Dev shell

```bash
nix develop
```

You get the locked Python environment plus `uv`, `ruff` and `pytest`, with
`PYTHONPATH` pointed at the repo. The dev shell installs no Git hooks: the checks run from `.githooks/`
(see [Quality gates](quality-gates.md)).

Use `uv` for lock operations only (`uv add …`, `uv lock`) — never
`uv sync`/`uv run` inside the shell; Nix owns the environment.

```bash
uv lock --check
pytest tests/integration/ -q
```

## Run

```bash
AUTH_MODE=none nix run .#axmem -- --transport http --host 127.0.0.1 --port 8020
```

(`nix run .` works too — `axmem` is the default app.) Anything after
`--` is passed to Axmem; see `axmem --help` for transports and flags.
For MCP clients, point a stdio server at `nix run .#axmem` with `AUTH_MODE=none` in its environment. The server refuses to start without `AUTH_MODE`, and accepts `none` only on stdio or a loopback bind.

## Build

```bash
nix build .#axmem -o result-axmem   # wrapped app package
```

## Versions

Hatch-vcs has no `.git` inside Nix builds, so the version defaults to
`0.0.0+dev`. For a real tag, inject it release-side:

```bash
AXMEM_VERSION=$(git describe --tags) nix build --impure .#axmem
```

## Checks

The flake defines no check, formatter or hook of its own: every check of the project is a gate of
`scripts/quality`, which the Git hooks in `.githooks/` run (see [Quality gates](quality-gates.md)). `nix fmt` and
`nix flake check` do nothing for it.

## Tests

The integration suite needs network (embedding/model downloads), so it runs in
the dev shell rather than the sandboxed flake check:

```bash
nix develop -c pytest tests/integration/ -q
```

The tests run the real TensorRT embedder and reranker, so the host needs an NVIDIA GPU and driver R580 or newer
(see [GPU models](gpu-models.md)). The Nix build of the locked environment with the TensorRT and CUDA wheels
(`tensorrt-cu13*`, `cuda-python`, from the NVIDIA package index) has not been verified: Nix was not available when the
GPU models landed. On NixOS the CUDA driver libraries must also be visible to the process (for example through
`hardware.nvidia` and `LD_LIBRARY_PATH`).

## Troubleshooting

- `warning: Git tree … is dirty` — benign; Nix tells you the evaluation used
  your worktree state. Commit for byte-identical reproducibility.
- `pre-commit: command not found` in an old shell — re-enter `nix develop`
  (the shell installs hooks and PATH on entry).
- `aarch64` packages evaluate but won't *build* here — that needs an aarch64
  builder or CI (`nix build --dry-run` proves the closure resolves).
- First `nix develop`/`nix build` downloads a lot (toolchain + all locked
  wheels) — subsequent runs are cached.
