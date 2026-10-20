# Offline and HuggingFace-Free Setup Guide

This guide explains how to run Axmem on a host without internet access, or where HuggingFace is blocked by a corporate firewall.

## Use Case

The default embeddings provider (`EMBEDDING_PROVIDER=TensorRT`) and the TensorRT reranker run in the server process on the GPU. Each of them reads two files, `onnx/model.onnx` and `tokenizer.json`, from a model directory. The first start fetches the files from HuggingFace; every later start, and every start on a host where the directory already holds the files, needs no network at all.

**What works normally:**
- Installing the program and its dependencies from PyPI and the NVIDIA package index (`uv sync`)
- Package updates

**What needs pre-seeding on an offline host:**
- The embedding model directory (`EMBEDDING_MODEL_DIR`)
- The reranking model directory (`RERANKING_MODEL_DIR`)
- The token-counting data of tiktoken (see [Startup without internet](#startup-without-internet))

## Model directories

| Setting | Default | Holds |
|---|---|---|
| `EMBEDDING_MODEL_DIR` | `models/embedder` in the platform user data directory (Linux: `~/.local/share/forgetful/models/embedder`) | `BAAI--bge-small-en-v1.5/onnx/model.onnx`, `tokenizer.json`, `manifest.json` |
| `RERANKING_MODEL_DIR` | `models/reranker` in the same place | `Xenova--ms-marco-MiniLM-L-12-v2/onnx/model.onnx`, `tokenizer.json`, `manifest.json` |
| `EMBEDDING_ENGINE_CACHE_DIR`, `RERANKING_ENGINE_CACHE_DIR` | `models/tensorrt` in the same place | TensorRT engines, rebuilt on the host in a few seconds |

The directory of a model is named after the repository id with `/` replaced by `--`. Files that are
already there are never downloaded. A `manifest.json` records the SHA-256 of both files on first use, and a later start whose files
no longer match refuses to run. Copy only the model directories: engines belong to one GPU and driver.

## Pre-seeding an offline host

On a connected host, after one successful start (or `forgetful --re-embed --dry-run`, which starts the embedder):

```bash
cd ~/.local/share/forgetful/models/
tar -czf axmem-models.tar.gz embedder/ reranker/
```

Transfer the archive (USB, network share, ...) and on the offline host:

```bash
mkdir -p ~/.local/share/forgetful/models/
tar -xzf axmem-models.tar.gz -C ~/.local/share/forgetful/models/
```

Then set `HF_HUB_OFFLINE=1` so that nothing tries the network. A file that is missing is reported
at start as `Error: the embedder cannot start: model-unavailable` (or `the reranker`) naming the
model directory, instead of waiting for a connection that never comes:

```bash
HF_HUB_OFFLINE=1 AUTH_MODE=none uv run --no-sync main.py serve
```

The server refuses to start without `AUTH_MODE`; `AUTH_MODE=none` serves one local tenant on stdio or a loopback bind. The commands here leave it out for brevity: set it in the environment or in `~/.config/forgetful/.env`.

To keep the models somewhere else, set the directories explicitly (changing a path does not move existing files; copy them first):

```dotenv
EMBEDDING_MODEL_DIR=/opt/models/embedder
RERANKING_MODEL_DIR=/opt/models/reranker
EMBEDDING_ENGINE_CACHE_DIR=/opt/models/tensorrt
RERANKING_ENGINE_CACHE_DIR=/opt/models/tensorrt
```

## Startup without internet

Install Axmem and its Python dependencies while internet is available (`uv sync` from a
checkout). Starting from an already synced checkout with `uv run --no-sync main.py` does not
contact PyPI or rebuild the package.

Memory queries also use tiktoken's token-counting data, which uses a separate cache
(`TIKTOKEN_CACHE_DIR`, read by tiktoken itself). Without it, queries still work but budget tokens
with an approximate character-based estimate. Populate it while internet is available and keep it
on persistent storage:

```bash
uv run python -c 'from app.utils.token_counter import TokenCounter; TokenCounter()'
```

```dotenv
TIKTOKEN_CACHE_DIR=/opt/models/tiktoken
```

Fully offline operation also requires a locally reachable database and authentication service, as
applicable. The embeddings provider and the reranker are local in the default setup, so the server
makes no call to any other host.

## Verification

Check that the model files are present:

```bash
find ~/.local/share/forgetful/models/embedder ~/.local/share/forgetful/models/reranker -name "*.onnx" -o -name tokenizer.json
# .../embedder/BAAI--bge-small-en-v1.5/onnx/model.onnx
# .../embedder/BAAI--bge-small-en-v1.5/tokenizer.json
# .../reranker/Xenova--ms-marco-MiniLM-L-12-v2/onnx/model.onnx
# .../reranker/Xenova--ms-marco-MiniLM-L-12-v2/tokenizer.json
```

Then start the server with `HF_HUB_OFFLINE=1`; the log shows one `embedder ready in ...` and one
`reranker ready in ...` line when both models are up on the GPU (see [GPU models](gpu-models.md)).

## Custom Models

To use different models, set a HuggingFace repository id that holds `onnx/model.onnx` and `tokenizer.json`:

```bash
EMBEDDING_MODEL=BAAI/bge-base-en-v1.5
EMBEDDING_DIMENSIONS=768
RERANKING_MODEL=cross-encoder/ms-marco-MiniLM-L-6-v2
```

**Note:** Custom models are downloaded from HuggingFace on first use (or must be placed in their
model directory by hand), and a different embedding model or width needs `forgetful --re-embed`
([embedding migration](embedding_migration.md)).

## FastEmbed provider (CPU)

`EMBEDDING_PROVIDER=FastEmbed` is still selectable and uses its own cache, which is independent of the
model directories above. FastEmbed normally auto-downloads `BAAI/bge-small-en-v1.5` from HuggingFace on first run.

The cache lives in `FASTEMBED_CACHE_DIR` (Linux `~/.local/share/forgetful/models/fastembed/`, macOS
`~/Library/Application Support/forgetful/models/fastembed/`, Windows `%LOCALAPPDATA%\forgetful\models\fastembed\`).
Where HuggingFace is blocked but GitHub is reachable, the pre-packaged model can be fetched from the project's
releases and extracted into it:

```bash
wget https://github.com/scottrbk/forgetful/releases/download/models-v1.0.0/fastembed-models.tar.gz
mkdir -p ~/.local/share/forgetful/models/
tar -xzf fastembed-models.tar.gz -C ~/.local/share/forgetful/models/
```

The result has the shape `fastembed/models--BAAI--bge-small-en-v1.5/snapshots/[hash]/model_optimized.onnx`.
To transfer a cache from another machine, archive its `fastembed/` directory and extract it the same way.

To fail fast instead of attempting network downloads when the model is missing, set
`FASTEMBED_LOCAL_FILES_ONLY=true`; the embedding model must then already be present under
`FASTEMBED_CACHE_DIR`, and Forgetful exits with an error that points to the cache directory and the
configured model name. The setting does not affect the TensorRT models.

## Troubleshooting

### Models not found
If the server can't find the models:
1. Verify the model directory exists and is readable by your user
2. Check that the repository directory holds `onnx/model.onnx` and `tokenizer.json` (the directory name is the repository id with `/` replaced by `--`)
3. Check that `EMBEDDING_MODEL_DIR` / `RERANKING_MODEL_DIR` point to the correct location (if using a custom path), and that `EMBEDDING_MODEL` / `RERANKING_MODEL` name the repository you copied
4. A `manifest.json` that no longer matches the files makes the start refuse with `model-unavailable`; restore the files, or delete the model directory named in the message so it is fetched again

### Unexpected downloads
If a model is downloaded although you have it:
- Verify the files are in the right subdirectory (see the table above)
- Check the logs to see which directory is being used
- Ensure you're using the default model names (or have placed your custom models)

## Support

For issues or questions:
- GitHub Issues: https://github.com/scottrbk/forgetful/issues
