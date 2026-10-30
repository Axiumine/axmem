# UAT Walkthrough Harness

Pre-release user-acceptance testing for Axmem's agent-facing contract. A real agent
(driven headlessly via [agent-shell](https://pypi.org/project/agent-shell-py/)) works
through each canonical skill in `skills/` against a throwaway local Axmem — does the
doc make sense, do the commands behave as documented — and reports issues. The harness
captures the agent's report *and* the full event transcript; acceptance is decided by a
supervising review of both, never by the agent's self-report alone.

The agent runs **inside a sandbox container** (pattern ported from eval-harness's
DockerRunner): it can only see the mounted skill artifacts and staged credential
copies, so the host filesystem — and the real knowledge base — are out of reach by
construction, not by convention. The throwaway server stays on the host, where the
TensorRT model directory is warm.

## Prerequisites

- Docker running, with Docker Desktop as the supported setup: the agent reaches the
  host-side throwaway server (bound to `127.0.0.1`) through `host.docker.internal`,
  which only Docker Desktop routes to a loopback listener (the server has no credential, so it answers that
  host name only because the harness lists it in `AXMEM_ALLOWED_HOSTS`). On Docker Engine for Linux
  the name resolves (the harness maps it to the host gateway) but the bridge cannot
  reach a loopback-only server, so the run stops at the reachability preflight with
  exit code `2` instead of walking skills against a dead server
- `uv sync --group harness` (installs the `docker` SDK — agent-shell lives in the
  container image, not on the host)
- Opencode authenticated on the host (`opencode auth login`); the harness stages a
  *copy* of `~/.local/share/opencode/auth.json` into the container

## Running

```bash
uv run python -m test_harness                                  # full 6-skill walkthrough
uv run python -m test_harness --skills axmem-recall        # subset (canonical order)
uv run python -m test_harness --timeout 600 --output /tmp/uat
uv run python -m test_harness --rebuild-image                  # force image rebuild
```

Flags > `TEST_HARNESS_*` env > defaults (`test_harness/config.py`). `--skills` and
`TEST_HARNESS_SKILLS` share one comma-separated spelling (`a,b`; spaces around commas are
ignored; an empty value is an error, never "all skills"). `--timeout` /
`TEST_HARNESS_SKILL_TIMEOUT` applies to every skill, including encode-repo, whose longer
1800s default only applies when no timeout was given; it must be at least 1 second.

The walkthrough order
is fixed by data dependencies — write-skills populate the store the read-skills then
query: `cli-setup → remember → recall → explore →
context-gather → encode-repo`. `axmem-mcp-setup` is not walked: the MCP
surface pass is a reserved axis (`--surface` accepts only `cli` in v1). `--agent` accepts
only `opencode` in v1 — other agents need provisioning ported in `container.py`.

Exit codes: `0` every session completed (issues found are findings, not failures),
`1` some session timed out or errored (partial UAT), `2` harness infrastructure failure
or invalid usage/configuration (a one-line `usage error: ...` on stderr, nothing started).

## The image

`axmem-test-harness:latest`, built from `test_harness/docker/Dockerfile`: opencode (version pinned in
`test_harness/docker/package.json` and `package-lock.json`, installed with `npm ci`), uv-managed Python 3.14,
agent-shell, and Axmem's **locked dependency tree** baked as cached layers. The working tree itself is built as
a wheel on the host and installed `--no-deps` at container start, so every run walks the current checkout without
an image rebuild. `ensure_image()` hashes `uv export --frozen`, the Dockerfile, `package.json` and
`package-lock.json` into an image label and rebuilds automatically when any of them changes
(stale images have burned runs before), so a new `node` digest or a new `opencode-ai` pin
takes effect on the next run. `--rebuild-image` forces a rebuild without the layer cache
(`docker build --no-cache`), so the floating installs (`agent-shell-py`, the `uv` installer)
really pick up a new release. To move opencode, change the version in `package.json` and
refresh `package-lock.json` as the comment in the Dockerfile describes.

The container runs as the invoking host user (`uid:gid`, `HOME=/home/node`) and the image
leaves the venv and `/home/node` writable for any uid, so the rw mounts and the staged
credentials (owner-only: 0700 dir, 0600 files) work on a host whatever its uid. Images
built before that change only support a host uid of 1000; run `--rebuild-image` once on
other hosts. Rootless Docker/podman, where the host user is mapped to container root, is
not covered: there the container uid no longer owns the bind mounts.

## What a run does

1. Build/verify the sandbox image, boot `axmem serve` as a host subprocess:
   against the operator's Qdrant (`QDRANT_URL` and `QDRANT_API_KEY` of the harness environment, so a
   Qdrant node must already run) in collections of a prefix of their own, `AUTH_MODE=none` on the
   loopback bind, TensorRT embeddings (warm default model directory), `/health` polled to 200. The
   run's collections are deleted at teardown. Every other Axmem
   setting is pinned to its default, so shell variables and
   `~/.config/axmem` cannot leak into the instance under review; the server is the
   `axmem` next to the interpreter running the harness, and the harness reaches it
   directly (no `HTTP(S)_PROXY`).
2. Start one container for the run — fenced env baked in (`AXMEM_SERVER` →
   `host.docker.internal:<port>`, decoy `QDRANT_URL` for any `--local` detour), staged
   opencode credentials, `runs/<ts>/skills/` mounted rw, the wheel + runner mounted ro —
   then two in-container preflights, cheapest first: a stdlib GET of the throwaway
   server's `/health` (unreachable server = infra error, so no session ever burns model
   time on connection errors) and the agent health check (broken agent = infra error,
   not findings).
3. Per skill, in order: fresh agent session (one `docker exec` each) in a prepared
   workspace (`SKILL.md` copy; `fixture-repo/` for encode-repo), wrapped in an
   OS-level `timeout --kill-after` — host-side cancellation cannot kill a process
   inside a container.
4. Teardown container and server, then a summary table and `run.json`. Teardown is
   best-effort and independent per resource (a docker failure never skips stopping the
   server or deleting the staged credential copies), and `SIGTERM` (`kill`, a CI cancel,
   `timeout`) takes the same path as Ctrl-C: the run is cancelled at its next await,
   everything is torn down, and the process exits `143`. A Ctrl-C during container
   startup also removes a container that the start thread creates after the fact.

## Artifacts

```
test_harness/runs/<timestamp>-<agent>/
├── run.json               # per-skill summary: status, verdict, issues, breaches, cost
├── server.log             # throwaway server output (host-side, not visible in-container)
├── harness/               # the ro /harness mount: working-tree wheel + runner.py
└── skills/<skill>/        # the rw /skills mount, one dir per walked skill
    ├── prompt.txt         # exactly what the agent was asked
    ├── events.jsonl       # normalized StreamEvents, flushed as they arrived
    ├── debug.log          # agent-shell raw DEBUG stream (full command args)
    ├── session.log        # runner stdout/stderr from the docker exec
    ├── meta.json          # status, timings, cost, tokens, report status, breaches
    └── workspace/         # the agent's cwd: SKILL.md, report.json, scratch files
```

## Supervising analysis (the acceptance step)

The run is evidence collection; acceptance is a review. For each skill:

1. Read `workspace/report.json` (missing/malformed report is itself a finding — the
   contract wasn't followable).
2. Cross-check every claim against `events.jsonl` and `debug.log`: did the commands the
   report cites actually run, did the outputs match the quoted evidence? The transcript
   owns the truth.
3. Classify surviving issues: doc defect / product defect / model noise.
4. Check `meta.json` breach flags — any flagged tool name means the agent reached a
   configured axmem MCP server instead of the fenced CLI route. Grep `debug.log`
   for `--local` while you're there (the decoy makes it harmless, but it signals the
   doc failed to keep the agent on the connected-server route).

Then deliver the acceptance verdict with the classified issue list.

## Isolation notes

Three independent fences, outermost first:

- **The container** is the hard barrier: the host filesystem simply isn't mounted
  (only `runs/<ts>/skills/`, the ro harness dir, and staged credential *copies*), so
  no tool the agent wields can touch the real repo, home directory, or knowledge base.
- **Env fencing** still rides inside it (mem 1701: fence both routes) — the container
  is born with `AXMEM_SERVER` pointing at the throwaway and `QDRANT_URL` at a
  decoy that nothing listens on, and with no real credentials to leak at all.
- **Tool scoping + breach scan** remain defence-in-depth: agent-shell's tool lists do
  not enforce under auto-approve, so the post-run scan flags any axmem MCP tool
  use in the transcript.

The `skills/` mount is the one tree both sides write, so the agent can plant symlinks in
it (including in the directories of skills that have not run yet). Host-side reads and
writes under it go through `safe_io.py`: a symlink is never followed — writes replace it
with a regular file, reads ignore it (a symlinked `report.json` is reported as missing) —
and the encode-repo fixture is committed in a private temp dir and copied in as plain
data, so git never runs inside the agent-writable tree.
