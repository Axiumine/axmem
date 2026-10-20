# GPU Reranking

Local reranking runs the cross-encoder directly on an NVIDIA GPU with TensorRT 11, in strict fp32.
It does not use ONNX Runtime, and it has **no CPU path, no fallback and no skip**. A server whose
GPU or TensorRT libraries are not usable refuses to start and says why. The two ways to run
without a GPU are explicit choices: `RERANKING_PROVIDER=HTTP` (a remote rerank service) or
`RERANKING_ENABLED=false` (no reranking).

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
engine fingerprint, so a bump rebuilds the cached engine once on the next start.

To bump: change the three pins, run `uv lock`, then run the whole test suite on a GPU host. The
golden-score tests in `tests/integration/test_tensorrt_reranker.py` compare the scores with values
recorded from the fp32 model, so a numerical change in a new release fails visibly instead of
shipping. Move to a new minor release only after that passes; never loosen the tolerances to make
a bump pass.

## Precision

The engine is built in strict fp32. fp16 and bf16 are not supported at all.

`RERANKING_TF32=true` lets the build use TF32 tensor-core math. TF32 keeps the 8-bit exponent of
fp32 but computes the multiplications with a 10-bit mantissa (fp32 has 23). It is faster on recent
NVIDIA GPUs and no longer bit-exact fp32, so it is off by default and the TensorRT TF32 builder
flag is cleared explicitly when it is off (TensorRT enables it by default otherwise).

On the reference host (RTX PRO 4000 Blackwell) the strict fp32 engine scores 20 documents in about
38 ms and differs from the CPU fp32 scores of the same model by at most 1e-5.

## Engine cache

On the first start the ONNX model is converted into a TensorRT engine for the installed GPU. That
takes about 5 to 7 seconds on the reference host, and loading a cached engine takes well under a
second. Engines live in `RERANKING_ENGINE_CACHE_DIR`, one directory per fingerprint. The
fingerprint is made of:

- the SHA-256 of the model file
- the TensorRT version
- the GPU name and its compute capability
- the major version of the NVIDIA driver
- `RERANKING_TF32`
- the optimization profile (batch 1 to 64, sequence length 2 to 512)

Any change rebuilds the engine at the next start. The startup log names the cause, for example
`Building the TensorRT engine (fingerprint changed: tensorrt (11.3.0.1 -> 11.4.0.106))`,
and, once the new engine exists, lists the removed directories of other fingerprints
(`Pruned stale TensorRT engine directories: ...`). A damaged or unreadable cache entry is
discarded with a warning and rebuilt. Concurrent starts of several server processes serialize on
a lock file in the cache directory, so the engine is built once.

Every start logs one line with the warm-up result:

```
TensorRT 11.4.0.106 reranker ready in 0.31s: engine cache hit; NVIDIA RTX PRO 4000 Blackwell (24 GiB), compute capability 12.0, driver 615.71.09; strict fp32; 1 worker(s); /.../models/tensorrt/3f9a...
```

The warm-up includes scoring one query and document pair, so a server that reports ready has
already run the engine on the GPU.

A search scores up to 64 documents per engine call; more candidates (`DENSE_SEARCH_CANDIDATES`
above 64) are scored in chunks.

## GPU memory

| Configuration | GPU memory |
|---|---|
| One server process, `RERANKING_WORKERS=1` | about 2.2 GiB |
| Each further worker | about 1.8 GiB more |
| After the adapter is closed | about 0.2 GiB (CUDA context) |

An execution context reserves the activation memory of the largest shape in the profile, which is
why a worker costs about 1.8 GiB. Every server process loads its own engine, so size
`RERANKING_WORKERS` and the number of processes against the free memory of the GPU. The test
suite checks this before it runs (see [Contributors](contributors.md)).

## Model files and offline hosts

The model is fetched once from Hugging Face into `RERANKING_MODEL_DIR`: `onnx/model.onnx` and
`tokenizer.json`, at a pinned revision for the default model. A `manifest.json` records their
SHA-256 on first use; a later start whose files no longer match refuses to run instead of
building an engine from changed bytes. Files that are already present are never downloaded, so
for a host without internet access copy the directory from a connected host (or place `onnx/model.onnx` and
`tokenizer.json` in `<RERANKING_MODEL_DIR>/Xenova--ms-marco-MiniLM-L-12-v2/`).

## Startup refusal causes

| Cause | Meaning | Fix |
|---|---|---|
| `no-gpu` | CUDA reports no device (also `CUDA_VISIBLE_DEVICES=-1`) | Expose an NVIDIA GPU, or use `RERANKING_PROVIDER=HTTP` or `RERANKING_ENABLED=false` |
| `driver` | The driver does not support CUDA 13 | Install driver R580 or newer, reload the kernel modules |
| `gpu-error` | CUDA or NVML failed while probing the device | Check `nvidia-smi` and the kernel log |
| `tensorrt-missing` | TensorRT or the CUDA Python bindings cannot be imported | Run `uv sync`; see the hint in the message |
| `model-unavailable` | Model files cannot be fetched or fail their SHA-256 record | Restore network access, or delete the model directory named in the message |
| `build-failed` | TensorRT could not build the engine | Read the TensorRT error in the message |
| `engine-unusable` | TensorRT built an engine but cannot create an execution context or run it | Delete the engine cache directory; if it repeats, report the TensorRT version and GPU |

The message is printed as `Error: the reranker cannot start: <cause>` and the exit code is 1. The
server does this before it binds a port; the `forgetful` CLI in local mode reports the same error.
