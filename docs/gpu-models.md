# GPU Models: Embeddings and Reranking

Axmem computes embeddings and reranks search results on the NVIDIA GPU of the machine it runs on,
with TensorRT 11, in strict fp32. Neither model uses ONNX Runtime, and there is **no CPU path, no
fallback and no skip** for them. A server whose GPU or TensorRT libraries are not usable refuses to
start and says why.

| Model | Setting | Default | What it does |
|---|---|---|---|
| Embedder | `EMBEDDING_PROVIDER=TensorRT` | `BAAI/bge-small-en-v1.5`, 384 dimensions | Turns every memory and every query into a vector |
| Reranker | `RERANKING_PROVIDER=TensorRT` | `Xenova/ms-marco-MiniLM-L-12-v2` | Re-orders the search candidates with a cross-encoder |

The ways to run without the GPU are explicit choices: another embeddings provider
(`EMBEDDING_PROVIDER=gateway`, `OpenAI`, `Azure`, `Google`, `Ollama`), `RERANKING_PROVIDER=HTTP` (a
remote rerank service) or `RERANKING_ENABLED=false` (no reranking). Nothing switches to them by
itself.

Both models run in one process on one CUDA context, so the GPU memory of the pair is the number to
plan with (see [GPU memory](#gpu-memory)).

## Host requirements

| Requirement | Version |
|---|---|
| GPU | NVIDIA, compute capability supported by TensorRT 11 |
| NVIDIA driver | R580 or newer (CUDA 13 capable) |
| CUDA runtime and TensorRT | installed by `uv sync` from the lock file, nothing to install system-wide |
| Operating system | Linux (the engine cache locks with `fcntl`) |
| Python | 3.14 |

The CUDA 13 runtime (`cuda-python`) and TensorRT (`tensorrt-cu13`, `tensorrt-cu13-bindings`,
`tensorrt-cu13-libs`) are locked in `uv.lock`. They come from the NVIDIA package index
(`https://pypi.nvidia.com`, declared in `pyproject.toml`), because on PyPI `tensorrt-cu13` and
`tensorrt-cu13-libs` are placeholders (source packages that fetch the real files from that index while they build);
only `tensorrt-cu13-bindings` is a real wheel there. `tensorrt-cu13-libs` is a download of about 3.8 GB.

`uv sync`, `uv tool install .` and uv's install from a Git URL read that index from `pyproject.toml`. A built wheel cannot carry it: its metadata names the three packages with their pins and no index, so a wheel installed with pip or `uv tool install` needs `--extra-index-url https://pypi.nvidia.com`, which resolves all three to NVIDIA's files (the README lists the commands in [Installing the package](../README.md#installing-the-package)). Without the flag the installer takes the two PyPI placeholders (`tensorrt-cu13`, `tensorrt-cu13-libs`), which build by fetching the NVIDIA files, so the install needs `pypi.nvidia.com` to be reachable.

Only the driver belongs to the host. A driver older than R580 fails at start with the cause
`driver`; no visible GPU (including `CUDA_VISIBLE_DEVICES=-1`) fails with `no-gpu`.

### TensorRT version pin and bump policy

TensorRT is pinned to one exact 11.x release (`11.4.0.106` at the time of writing) in
`pyproject.toml`, and the three packages move together. The TensorRT version is part of the
engine fingerprint, so a bump rebuilds the cached engines once on the next start.

To bump: change the three pins, run `uv lock`, then run the whole test suite on a GPU host. The
golden tests in `tests/integration/test_tensorrt_embedder.py` (embedding vectors) and
`tests/integration/test_tensorrt_reranker.py` (rerank scores) compare the outputs with values
recorded from the fp32 models on the CPU, so a numerical change in a new release fails visibly
instead of shipping. Move to a new minor release only after that passes; never loosen the
tolerances to make a bump pass.

## The embedder

`EMBEDDING_PROVIDER=TensorRT` is the default. A text becomes one vector the way the bge models
define it:

1. The tokenizer (`tokenizer.json` of the model, run by the `tokenizers` library) adds `[CLS]` and
   `[SEP]` and truncates to 512 tokens. Text beyond 512 tokens is cut off, not rejected.
2. A batch is padded to its longest text (never to fewer than 2 tokens), and the padding is masked.
3. The hidden state at `[CLS]` is the embedding (CLS pooling).
4. The vector is scaled to length 1 (L2 normalisation), so the dot product of two vectors is their
   cosine similarity.

The engine is built for batches of 1 to 64 texts and sequences of 2 to 512 tokens (optimum 16 by
128). `generate_embeddings` embeds a list in chunks of at most 64 texts, so a re-embedding batch of
any size works; empty or non-string texts are refused with an error that names the position.
There is one execution context, so calls run one at a time on one worker thread and never block the
event loop. Every TensorRT call is checked, and a vector with a non-finite value is an error, not a
stored memory.

The model is `EMBEDDING_MODEL` (default `BAAI/bge-small-en-v1.5`). The ONNX file and the tokenizer
are fetched once into `EMBEDDING_MODEL_DIR` at a pinned revision. For this model the Hugging Face
repositories `BAAI/bge-small-en-v1.5` and `Xenova/bge-small-en-v1.5` hold byte-identical
`onnx/model.onnx` (SHA-256 `828e1496d7fabb79cfa4dcd84fa38625c0d3d21da474a00f08db0f559940cf35`) and
`tokenizer.json` files, and both revisions are pinned. Other bge-style models can be selected with
`EMBEDDING_MODEL` when they publish `onnx/model.onnx` and `tokenizer.json`; set
`EMBEDDING_DIMENSIONS` to the width they produce.

`EMBEDDING_DIMENSIONS` (default `384`) is checked at start: the warm-up embeds one text and compares
the width of the vector with the setting. A mismatch refuses to start with the cause `dimensions`
and releases the engine again. Memories that are already stored need `axmem --re-embed` after a
model or width change ([embedding migration](embedding_migration.md)).

## The reranker

`RERANKING_PROVIDER=TensorRT` runs the cross-encoder `RERANKING_MODEL` the same way: the
tokenizer encodes the query and the document as a pair, a batch is padded to its longest pair, the
engine returns one logit per pair, and the logits are the scores. A search scores up to 64
documents per engine call; more candidates (`DENSE_SEARCH_CANDIDATES` above 64) are scored in
chunks. `RERANKING_WORKERS` sets how many execution contexts (and worker threads) serve
concurrent searches.

## Precision

The engines are built in strict fp32. fp16 and bf16 are not supported at all.

`EMBEDDING_TF32=true` and `RERANKING_TF32=true` let the build of the respective engine use TF32
tensor-core math. TF32 keeps the 8-bit exponent of fp32 but computes the multiplications with a
10-bit mantissa (fp32 has 23). It is faster on recent NVIDIA GPUs and no longer bit-exact fp32, so
both are off by default and the TensorRT TF32 builder flag is cleared explicitly when it is off
(TensorRT enables it by default otherwise). Turning `EMBEDDING_TF32` on changes the stored vector
space slightly; run `axmem --re-embed` after changing it.

On the reference host (RTX PRO 4000 Blackwell) the strict fp32 embedder differs from the CPU fp32
vectors of the same model by about 2e-7 per component, and the strict fp32 reranker differs from the
CPU fp32 scores by at most 1e-5.

## Engine cache

On the first start the ONNX model is converted into a TensorRT engine for the installed GPU. That
takes about 6 to 9 seconds per model on the reference host, and loading a cached engine takes well
under a second. Engines live in `EMBEDDING_ENGINE_CACHE_DIR` and `RERANKING_ENGINE_CACHE_DIR` (the
same default directory, `models/tensorrt` in the user data directory), one directory per
fingerprint. The fingerprint is made of:

- the SHA-256 of the model file
- the TensorRT version
- the GPU name and its compute capability
- the major version of the NVIDIA driver
- the TF32 setting of the model
- the optimization profile (batch 1 to 64, sequence length 2 to 512)

Any change rebuilds the engine at the next start. The startup log names the cause, for example
`Building the TensorRT engine (fingerprint changed: tensorrt (11.3.0.1 -> 11.4.0.106))`,
and, once the new engine exists, lists the removed directories of other fingerprints of that model
(`Pruned stale TensorRT engine directories: ...`). An entry that has lost its plan or
its fingerprint file is damaged: it is discarded with a warning and rebuilt. A plan that TensorRT refuses is not taken
for a damaged file, because a GPU too full to hold the engine (or a driver that does not suit it) refuses an intact plan
alike. The start then builds a plan from the same model, and only when that one loads here is the refused entry replaced.
When TensorRT refuses the new plan too, the cached entry is kept, nothing new is stored, and the start fails with
`engine-unusable` and the TensorRT message: free GPU memory or fix the driver, then start again. A plan or fingerprint
file that cannot be read (permissions, I/O error) is not judged: the start fails with `engine-unusable` naming the
cache directory and the entry is left as it is. Concurrent starts of several server processes serialize on
a lock file in the cache directory, so an engine is built once.

Every start logs one line per model with the warm-up result:

```
TensorRT 11.4.0.106 embedder ready in 0.42s: engine cache hit; NVIDIA RTX PRO 4000 Blackwell (24 GiB), compute capability 12.0, driver 615.71.09; strict fp32; 1 worker(s); /.../models/tensorrt/3f9a...
TensorRT 11.4.0.106 reranker ready in 0.31s: engine cache hit; NVIDIA RTX PRO 4000 Blackwell (24 GiB), compute capability 12.0, driver 615.71.09; strict fp32; 1 worker(s); /.../models/tensorrt/8c21...
```

The warm-up embeds one text (and scores one query and document pair), so a server that reports
ready has already run both engines on the GPU.

## GPU memory

| Configuration | GPU memory |
|---|---|
| Embedder alone, in one server process | about 2.2 GiB |
| Embedder and reranker (`RERANKING_WORKERS=1`), one CUDA context | about 4.1 GiB |
| Each further reranker worker | about 1.8 GiB more |
| After the adapters are closed | about 0.2 GiB (CUDA context) |

An execution context reserves the activation memory of the largest shape in the profile, which is
why an engine costs about 1.8 to 2 GiB. The embedder and the reranker share the CUDA context of the
process, so the context itself is paid once. After both are loaded the server logs the figures of
the pair:

```
GPU memory with the embedder and reranker loaded: this process holds 4.09 GiB; the device has 4.30 of 24 GiB in use; one CUDA context (0x...) shared by the embedder and the reranker
```

If the two adapters ever report different CUDA contexts the server logs a warning instead. Every
server process loads its own engines, so size `RERANKING_WORKERS` and the number of processes
against the free memory of the GPU. The test suite checks this before it runs (see
[Contributors](contributors.md)).

## Model files and offline hosts

Both models run in the server process on the GPU, so a host needs the network only to fetch their
files once. Each model reads two files, `onnx/model.onnx` and `tokenizer.json`, from its own
directory. The first start fetches them from Hugging Face (at a pinned revision for the default
models); every later start, and every start on a host whose directory already holds the files,
needs no network at all.

| Setting | Default | Holds |
|---|---|---|
| `EMBEDDING_MODEL_DIR` | `models/embedder` in the platform user data directory (Linux: `~/.local/share/axmem/models/embedder`) | `BAAI--bge-small-en-v1.5/onnx/model.onnx`, `tokenizer.json`, `manifest.json` |
| `RERANKING_MODEL_DIR` | `models/reranker` in the same place | `Xenova--ms-marco-MiniLM-L-12-v2/onnx/model.onnx`, `tokenizer.json`, `manifest.json` |
| `EMBEDDING_ENGINE_CACHE_DIR`, `RERANKING_ENGINE_CACHE_DIR` | `models/tensorrt` in the same place | TensorRT engines, rebuilt on the host in a few seconds |

The directory of a model is named after the repository id with `/` replaced by `--`. Files that are
already there are never downloaded. A `manifest.json` records the SHA-256 of both files on first
use, and a later start whose files no longer match refuses to run instead of building an engine
from changed bytes. Copy only the model directories: engines belong to one GPU and driver.

### Pre-seeding an offline host

What an offline host needs beyond the installed program (`uv sync` from a checkout, while the
network is available; `uv run --no-sync main.py` then contacts neither PyPI nor a build):

- the embedding model directory (`EMBEDDING_MODEL_DIR`)
- the reranking model directory (`RERANKING_MODEL_DIR`)
- the token-counting data of tiktoken (below)

On a connected host, after one successful `axmem serve` start (which fetches both models;
`axmem --re-embed --dry-run` starts only the embedder, so it leaves `reranker/` out):

```bash
cd ~/.local/share/axmem/models/
tar -czf axmem-models.tar.gz embedder/ reranker/
```

Transfer the archive (USB, network share, ...) and on the offline host:

```bash
mkdir -p ~/.local/share/axmem/models/
tar -xzf axmem-models.tar.gz -C ~/.local/share/axmem/models/
```

Then set `HF_HUB_OFFLINE=1` so that nothing tries the network. A file that is missing is reported
at start as `Error: the embedder cannot start: cannot fetch onnx/model.onnx of <repository> ... copy the file
to <path>` (or `the reranker`) naming the file to copy, instead of waiting for a connection that never comes:

```bash
HF_HUB_OFFLINE=1 AUTH_MODE=none uv run --no-sync main.py serve
```

The server refuses to start without `AUTH_MODE`; `AUTH_MODE=none` serves one local tenant on stdio
or a loopback bind. Set it in the environment or in the user settings file
(`~/.config/axmem/` on Linux).

To keep the models somewhere else, set the directories explicitly (changing a path does not move
existing files; copy them first):

```dotenv
EMBEDDING_MODEL_DIR=/opt/models/embedder
RERANKING_MODEL_DIR=/opt/models/reranker
EMBEDDING_ENGINE_CACHE_DIR=/opt/models/tensorrt
RERANKING_ENGINE_CACHE_DIR=/opt/models/tensorrt
```

Memory queries also use tiktoken's token-counting data, which has a separate cache
(`TIKTOKEN_CACHE_DIR`, read by tiktoken itself; `HF_HUB_OFFLINE` does not cover it). The server loads that data
before it reports ready, and refuses to start when it cannot (see below). Populate it while the network is
available and keep it on persistent storage, with the variable set to the same directory for the pre-load and
for the server. tiktoken reads the variable from the process environment only, and Axmem never copies a value
from a settings file (the dotenv file next to the checkout or the one in the user settings directory) into the
environment, so a line there has no effect: set it in the shell that starts the server, the `Environment=` line
of its service unit or the `-e` option of its container.

```bash
TIKTOKEN_CACHE_DIR=/opt/models/tiktoken \
  uv run python -c 'from app.utils.token_counter import TokenCounter; TokenCounter()'

TIKTOKEN_CACHE_DIR=/opt/models/tiktoken HF_HUB_OFFLINE=1 AUTH_MODE=none uv run --no-sync main.py serve
```

A host whose `TIKTOKEN_CACHE_DIR` holds the data starts with no network: the encoding is read from disk and no
connection is opened. A host that does not have the data has to download it, and tiktoken's download has no timeout
of its own, so on a host whose outbound traffic is dropped rather than refused (a firewall that discards packets, a
network namespace without a route) it would wait for the operating system's connect timeout (about two minutes on
Linux defaults). The start-up therefore bounds the wait with `TIKTOKEN_LOAD_TIMEOUT_SECONDS` (default 30) and, when
the data does not load in that time or the download fails, ends with exit code 1 and one line that names
`TIKTOKEN_CACHE_DIR` and the pre-load command above. `serve` puts the name of the check in front of the cause:

```text
Error: the token counter cannot start: the tiktoken encoding of the token counter cannot be loaded (no answer within 30 seconds, ...
```

The commands that run the tools in process (`axmem memory ...`, `axmem call ...` or `axmem tools ...` without
`AXMEM_SERVER`) build the same runtime and refuse alike, with the same cause and no prefix:

```text
Error: the tiktoken encoding of the token counter cannot be loaded (no answer within 30 seconds, ...
```

A host without the data but with a working network downloads it within the limit and starts.

Fully offline operation also needs a locally reachable Qdrant and authentication service, as
applicable. The embeddings provider and the reranker are local in the default setup, so with the model
files and the tiktoken data on disk the server makes no call to any other host. Without the tiktoken
data the server tries to download it while it starts and refuses to start when that fails or takes longer than
`TIKTOKEN_LOAD_TIMEOUT_SECONDS` (see above).

### Verifying the files

```bash
find ~/.local/share/axmem/models/embedder ~/.local/share/axmem/models/reranker -name "*.onnx" -o -name tokenizer.json
# .../embedder/BAAI--bge-small-en-v1.5/onnx/model.onnx
# .../embedder/BAAI--bge-small-en-v1.5/tokenizer.json
# .../reranker/Xenova--ms-marco-MiniLM-L-12-v2/onnx/model.onnx
# .../reranker/Xenova--ms-marco-MiniLM-L-12-v2/tokenizer.json
```

Then start the server with `HF_HUB_OFFLINE=1`; the log shows one `embedder ready in ...` and one
`reranker ready in ...` line when both models are up on the GPU.

### Other models

To use different models, set a Hugging Face repository id that holds `onnx/model.onnx` and
`tokenizer.json`:

```bash
EMBEDDING_MODEL=BAAI/bge-base-en-v1.5
EMBEDDING_DIMENSIONS=768
RERANKING_MODEL=cross-encoder/ms-marco-MiniLM-L-6-v2
```

Other models are fetched from Hugging Face on first use (or must be placed in their model
directory by hand), and a different embedding model or width needs `axmem --re-embed`
([embedding migration](embedding_migration.md)).

### Troubleshooting

Models not found:

1. Verify that the model directory exists and is readable by your user.
2. Check that the repository directory holds `onnx/model.onnx` and `tokenizer.json` (the directory name is the repository id with `/` replaced by `--`).
3. Check that `EMBEDDING_MODEL_DIR` / `RERANKING_MODEL_DIR` point to the right place (with a custom path), and that `EMBEDDING_MODEL` / `RERANKING_MODEL` name the repository you copied.
4. A `manifest.json` that no longer matches the files makes the start refuse with `model-unavailable`; restore the files, or delete the model directory named in the message so it is fetched again.

A model is downloaded although you have it: verify that the files are in the right subdirectory
(table above), check the log for the directory in use, and make sure the model names are the
defaults or name the repositories you placed.

## Startup refusal causes

| Cause | Meaning | Fix |
|---|---|---|
| `no-gpu` | CUDA reports no device (also `CUDA_VISIBLE_DEVICES=-1`) | Expose an NVIDIA GPU, or choose another embeddings provider and `RERANKING_PROVIDER=HTTP` or `RERANKING_ENABLED=false` |
| `driver` | The driver does not support CUDA 13 | Install driver R580 or newer, reload the kernel modules |
| `gpu-error` | CUDA or NVML failed while probing the device (any step, after the device count too) | Check `nvidia-smi` and the kernel log |
| `tensorrt-missing` | TensorRT or the CUDA Python bindings cannot be imported | Run `uv sync`; see the hint in the message |
| `model-unavailable` | Model files cannot be fetched, read or recorded, fail their SHA-256 record, or `tokenizer.json` cannot be parsed | Restore network access, or delete the model directory named in the message |
| `build-failed` | TensorRT could not build the engine, or the model holds half-precision or quantised tensors (only fp32 and integer ones are accepted) | Read the TensorRT error in the message |
| `engine-unusable` | TensorRT built an engine but cannot load it, create an execution context or run it (also when it refuses a cached engine and a new one alike, which keeps the cached one), or the engine cache directory cannot be read or written | Delete the engine cache directory, or point the `*_ENGINE_CACHE_DIR` setting at a writable one; if it repeats, report the TensorRT version and GPU |
| `dimensions` | The embedder produces vectors of another width than `EMBEDDING_DIMENSIONS` | Set `EMBEDDING_DIMENSIONS` to the width in the message, or choose a model of the configured width |

The message is printed as `Error: the embedder cannot start: <message>` or
`Error: the reranker cannot start: <message>` and the exit code is 1; the message explains the cause in the
table but does not print its name. The server does this before it
binds a port, the embedder first; the `axmem` CLI in local mode and `axmem --re-embed`
report the same error.
