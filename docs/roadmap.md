# Roadmap

What the Axmem project intends to work on in the twelve months from November 2026 to October 2027, and what it does not
intend to do. Written on 2026-10-29. It is a plan, not a promise: Axmem is a small project with one
maintainer ([MAINTAINERS.md](../MAINTAINERS.md)), nothing here has a date or a commitment of delivery, and the order can
change. This page is reviewed at each release, and the list of features it draws from is
[docs/features_roadmap.md](features_roadmap.md).

Only things the project has already written down as intended are listed. The sources are the unchecked items of
[docs/features_roadmap.md](features_roadmap.md), the planned entries of [CHANGELOG.md](../CHANGELOG.md) and
[docs/changes-from-forgetful.md](changes-from-forgetful.md) (there are none at the moment) and the release notes in
[docs/release.md](release.md). Anything else is not planned, even if it would be welcome.

## Where Axmem stands

`0.6.0rc1`, the first Axmem release and a release candidate, is on PyPI. It is a standalone memory server for AI agents
that runs the embedder and the reranker on an NVIDIA GPU, keeps its data in Qdrant, and authenticates every request
(JWT, token introspection or static tokens). What it does and does not guarantee about security is in
[docs/security.md](security.md).

## Planned

Listed in the order of [docs/features_roadmap.md](features_roadmap.md), which is "in order of priority". No item has a
design, a date or an owner yet unless it says so.

| Item | What it is | Source | State |
|---|---|---|---|
| Stable `0.6.0` | The first version went out as a release candidate to settle the publishing route in the real service (trusted publisher, environment, rulesets); the stable `0.6.0` is the version after it | [docs/release.md](release.md#rehearsal-with-a-release-candidate) | Intended; follows the release candidate |
| Resources: audio, images, video | Memories that carry or refer to media. First item of the feature roadmap | [features_roadmap.md](features_roadmap.md) | Not started. What it means for a server that "does one job: store, link, rank and retrieve memories" (README) has to be decided first |
| Authorisation Code flow and Dynamic Client Registration | OAuth sign-in for MCP clients that expect it, on top of the existing token modes. The GitHub and Google OAuth providers were removed, so this is new work | [features_roadmap.md](features_roadmap.md) (Authentication) | Not started |
| OpenRouter embeddings adapter | Another remote embeddings provider beside Azure, Google, OpenAI, Ollama and the HTTP gateway | [features_roadmap.md](features_roadmap.md) (Embedding Adapters) | Not started |
| OpenTelemetry forwarding | Send telemetry to an OpenTelemetry collector. No OpenTelemetry code exists today | [features_roadmap.md](features_roadmap.md) (Logging and Telemetry) | Not started |

Any of these that lands changes behaviour, so it comes with a [CHANGELOG.md](../CHANGELOG.md) entry, an update of
[docs/changes-from-forgetful.md](changes-from-forgetful.md) and, for authentication, a review of
[docs/security.md](security.md) and the [assurance case](assurance-case.md).

## Kept up as the work of every release

These are not features; they are what the project already says it does.

* **Security reports** are handled as [SECURITY.md](../SECURITY.md) describes, and fixes ship as new versions through the
  signed release process in [docs/release.md](release.md).
* **Dependencies and the TensorRT pin** move through the gates: a bump is checked by `trivy` and by the golden tests on a GPU
  host, and a tolerance is never loosened to make a bump pass ([GPU models](gpu-models.md#tensorrt-version-pin-and-bump-policy)).
* **The documentation** is part of the product ([CONTRIBUTING.md](../CONTRIBUTING.md#ways-to-contribute)): this page, the
  security requirements and the architecture overview are updated when the code they describe changes.

## Not planned

These are decisions, not omissions. A request for one of them is closed.

| Not planned | Why, and where it is said |
|---|---|
| Support for hardware other than NVIDIA GPUs: CPU-only, AMD, Intel | The embedder and reranker run on TensorRT and there is no CPU path, by design ([GPU models](gpu-models.md), [CONTRIBUTING.md](../CONTRIBUTING.md#hardware)). A request for another device is closed |
| Windows | Linux only; the engine cache locks with `fcntl` ([GPU models](gpu-models.md#host-requirements)) |
| Apple silicon (macOS) from the maintainer | The one exception is a pull request with an [MLX](https://github.com/ml-explore/mlx) backend that runs natively on an Apple-silicon Mac and keeps the NVIDIA path passing; the maintainer decides whether to merge it. A backend on Core ML, Metal, PyTorch MPS, ONNX Runtime or the CPU, or an Intel Mac, is not accepted, and no other platform is planned or considered ([CONTRIBUTING.md](../CONTRIBUTING.md#hardware), [GOVERNANCE.md](../GOVERNANCE.md#decisions)) |
| fp16 or bf16 engines | The engines are built in strict fp32; reduced precision is not supported ([GPU models](gpu-models.md)) |
| A datastore other than Qdrant: SQL, SQLite, PostgreSQL | They were removed, and Qdrant is the only store ([changes from Forgetful](changes-from-forgetful.md)) |
| A container image or compose deployment | Axmem runs natively; Docker is only a test dependency ([AGENTS.md](../AGENTS.md), [architecture overview](../architecture.md)) |
| Task and plan trackers, a document or file store, skills, an entity graph, an activity stream, a user or project registry | Axmem does one job: store, link, rank and retrieve memories ([README](../README.md#standalone)) |
| Python older than 3.14 | 3.14 is the only supported version ([Quality gates](quality-gates.md)) |
| A GPU, Docker or self-hosted job in hosted CI | Hosted CI runs only the static gates; the GPU suites run in the `pre-push` hook ([AGENTS.md](../AGENTS.md#quality-gates)) |
| Merging the later commits of upstream Forgetful | Axmem was forked at one upstream commit and does not follow upstream ([CHANGELOG.md](../CHANGELOG.md)) |

## How this page is kept

When an item lands it is checked off in [docs/features_roadmap.md](features_roadmap.md), recorded in the changelog and
removed from the table above. When the maintainer decides on a new direction it is added here, with its source, before work
starts. If you want to propose something, open an issue ([CONTRIBUTING.md](../CONTRIBUTING.md#ways-to-contribute)).
