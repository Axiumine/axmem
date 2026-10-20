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
(`https://pypi.nvidia.com`, declared in `pyproject.toml`), because the PyPI projects of the same
names are placeholders. `tensorrt-cu13-libs` is a download of about 3.8 GB.

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
and releases the engine again. Memories that are already stored need `forgetful --re-embed` after a
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
space slightly; run `forgetful --re-embed` after changing it.

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
(`Pruned stale TensorRT engine directories: ...`). A damaged or unreadable cache entry is
discarded with a warning and rebuilt. Concurrent starts of several server processes serialize on
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

Each model is fetched once from Hugging Face into its own directory (`EMBEDDING_MODEL_DIR`,
`RERANKING_MODEL_DIR`): `onnx/model.onnx` and `tokenizer.json`, at a pinned revision for the
default models. A `manifest.json` records their SHA-256 on first use; a later start whose files no
longer match refuses to run instead of building an engine from changed bytes. Files that are
already present are never downloaded, so for a host without internet access copy the directory
from a connected host (or place `onnx/model.onnx` and `tokenizer.json` in
`<EMBEDDING_MODEL_DIR>/BAAI--bge-small-en-v1.5/` and
`<RERANKING_MODEL_DIR>/Xenova--ms-marco-MiniLM-L-12-v2/`) and set `HF_HUB_OFFLINE=1`. With
`HF_HUB_OFFLINE=1` a missing file is reported as `model-unavailable` and nothing tries the network.
See [offline setup](OFFLINE_SETUP.md).

## Startup refusal causes

| Cause | Meaning | Fix |
|---|---|---|
| `no-gpu` | CUDA reports no device (also `CUDA_VISIBLE_DEVICES=-1`) | Expose an NVIDIA GPU, or choose another embeddings provider and `RERANKING_PROVIDER=HTTP` or `RERANKING_ENABLED=false` |
| `driver` | The driver does not support CUDA 13 | Install driver R580 or newer, reload the kernel modules |
| `gpu-error` | CUDA or NVML failed while probing the device | Check `nvidia-smi` and the kernel log |
| `tensorrt-missing` | TensorRT or the CUDA Python bindings cannot be imported | Run `uv sync`; see the hint in the message |
| `model-unavailable` | Model files cannot be fetched or fail their SHA-256 record | Restore network access, or delete the model directory named in the message |
| `build-failed` | TensorRT could not build the engine | Read the TensorRT error in the message |
| `engine-unusable` | TensorRT built an engine but cannot create an execution context or run it | Delete the engine cache directory; if it repeats, report the TensorRT version and GPU |
| `dimensions` | The embedder produces vectors of another width than `EMBEDDING_DIMENSIONS` | Set `EMBEDDING_DIMENSIONS` to the width in the message, or choose a model of the configured width |

The message is printed as `Error: the embedder cannot start: <cause>` or
`Error: the reranker cannot start: <cause>` and the exit code is 1. The server does this before it
binds a port, the embedder first; the `forgetful` CLI in local mode and `forgetful --re-embed`
report the same error.
