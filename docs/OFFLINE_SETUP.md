# HuggingFace-Free Setup Guide

This guide explains how to use Forgetful in environments where HuggingFace is blocked by corporate firewalls or network restrictions.

## Use Case

Forgetful uses FastEmbed for local embeddings, which normally auto-downloads models from HuggingFace on first run. In corporate environments where HuggingFace is blocked but PyPI and GitHub are accessible, you can pre-download models from GitHub instead.

**What works normally:**
- Installing forgetful: `uvx forgetful-ai` or `pip install forgetful-ai`
- Package updates from PyPI

**What needs pre-caching:**
- The embedding model (normally from HuggingFace, pre-packaged on GitHub)
- The reranking model files of the TensorRT reranker (from HuggingFace; see [Reranking model](#reranking-model))

## Model Cache Location

Forgetful caches FastEmbed models in a persistent directory:

- **Linux**: `~/.local/share/forgetful/models/fastembed/`
- **macOS**: `~/Library/Application Support/forgetful/models/fastembed/`
- **Windows**: `%LOCALAPPDATA%\forgetful\models\fastembed\`

You can override this location with the `FASTEMBED_CACHE_DIR` environment variable.

## Required Models

Forgetful requires two models (total ~193MB):

1. **Embedding Model**: `BAAI/bge-small-en-v1.5` (~65MB)
   - Used for generating vector embeddings from text
   - Cached by FastEmbed in `FASTEMBED_CACHE_DIR`

2. **Reranking Model**: `Xenova/ms-marco-MiniLM-L-12-v2` (~129MB)
   - Used for reranking search results by relevance
   - Not part of the FastEmbed cache: the TensorRT reranker keeps its own files in
     `RERANKING_MODEL_DIR` (see [Reranking model](#reranking-model))

## Setup Options

### Option 1: Auto-Download (Requires HuggingFace Access)

If you have unrestricted internet access, Forgetful will automatically download models on first run:

```bash
AUTH_MODE=none uvx forgetful-ai
# First run: Downloading embedding models (~180MB). This may take a minute...
```

The server refuses to start without `AUTH_MODE`; `AUTH_MODE=none` serves one local tenant on stdio or a loopback bind. The commands below leave it out for brevity: set it in the environment or in `~/.config/forgetful/.env`.

Models are cached for future use. **This is the recommended approach for most users.**

### Option 2: Pre-Download from GitHub (HuggingFace Blocked)

For environments where HuggingFace is blocked, download pre-packaged models from GitHub:

```bash
# Download models from GitHub releases
wget https://github.com/scottrbk/forgetful/releases/download/models-v1.0.0/fastembed-models.tar.gz

# Extract to cache directory (Linux/macOS)
mkdir -p ~/.local/share/forgetful/models/
tar -xzf fastembed-models.tar.gz -C ~/.local/share/forgetful/models/

# Windows (PowerShell)
# mkdir $env:LOCALAPPDATA\forgetful\models
# tar -xzf fastembed-models.tar.gz -C $env:LOCALAPPDATA\forgetful\models\
```

After extraction, verify the directory structure:
```
~/.local/share/forgetful/models/fastembed/
└── models--BAAI--bge-small-en-v1.5/
    └── snapshots/
        └── [hash]/
            ├── config.json
            ├── model_optimized.onnx
            └── tokenizer files...
```

Then install and run forgetful normally:
```bash
uvx forgetful-ai
```

To fail fast instead of attempting network downloads when a model is missing,
enable local-files-only mode:

```bash
FASTEMBED_LOCAL_FILES_ONLY=true uvx forgetful-ai
```

In this mode the embedding model must already be present under `FASTEMBED_CACHE_DIR`.
If not, Forgetful exits with an error that points to the cache directory and configured
model name. The reranker is not affected by this setting (see below).

### Option 3: Transfer from Another Machine

If you have the models cached on another machine, you can transfer them:

```bash
# On source machine (with models already cached)
cd ~/.local/share/forgetful/models/
tar -czf fastembed-models.tar.gz fastembed/

# Transfer fastembed-models.tar.gz to target machine (USB, network share, etc.)

# On target machine
mkdir -p ~/.local/share/forgetful/models/
tar -xzf fastembed-models.tar.gz -C ~/.local/share/forgetful/models/
```

## Custom Cache Directory

To use a different cache location:

```bash
# Set environment variable
export FASTEMBED_CACHE_DIR=/opt/models/fastembed

# Run Forgetful
uvx forgetful-ai
```

Or create a `.env` file:

```bash
# .env
FASTEMBED_CACHE_DIR=/opt/models/fastembed
FASTEMBED_LOCAL_FILES_ONLY=true
```

## Reranking model

The TensorRT reranker (see [GPU reranking](gpu-reranking.md)) does not use the FastEmbed cache.
It keeps `onnx/model.onnx` and `tokenizer.json` of `RERANKING_MODEL` in `RERANKING_MODEL_DIR`
(default: `models/reranker` beside the FastEmbed cache), in a directory named after the
repository, for example `Xenova--ms-marco-MiniLM-L-12-v2/`. Files that are already there are
never downloaded, so an offline host needs no HuggingFace access once the directory has been
copied from a connected host:

```bash
# On a connected host, after one successful start
cd ~/.local/share/forgetful/models/
tar -czf reranker-model.tar.gz reranker/

# On the offline host
mkdir -p ~/.local/share/forgetful/models/
tar -xzf reranker-model.tar.gz -C ~/.local/share/forgetful/models/
```

Copy only the model directory. Built TensorRT engines (`RERANKING_ENGINE_CACHE_DIR`) belong to
one GPU and driver and are rebuilt on the offline host in a few seconds.

## Startup without internet

Install Forgetful and its Python dependencies while internet is available (`uv sync` from a
checkout, or `uvx forgetful-ai`). Starting from an already synced checkout with
`uv run --no-sync main.py` does not contact PyPI or rebuild the package.

The embedding model and the reranking model files are downloaded separately. Memory queries also use
tiktoken's token-counting data, which uses a separate cache (`TIKTOKEN_CACHE_DIR`, read by
tiktoken itself). Without it, queries still work but budget tokens with an approximate
character-based estimate. Keep both caches on persistent storage, for example:

```dotenv
FASTEMBED_CACHE_DIR=/opt/models/fastembed
TIKTOKEN_CACHE_DIR=/opt/models/tiktoken
```

Normal startup downloads the configured FastEmbed model and the reranking model files. While internet is available,
populate the token-counting cache too:

```bash
uv run python -c 'from app.utils.token_counter import TokenCounter; TokenCounter()'
```

After preparation, set `FASTEMBED_LOCAL_FILES_ONLY=true` to require the cached embedding model.
Changing paths does not move existing cached files; copy or download them into the new
directories first.

Fully offline operation also requires locally reachable database, embedding/reranking and
authentication services, as applicable.

## Verification

To verify models are cached correctly, check for the presence of `.onnx` files:

```bash
# Linux/macOS
find ~/.local/share/forgetful/models/fastembed -name "*.onnx"

# Should show:
# ~/.local/share/forgetful/models/fastembed/models--BAAI--bge-small-en-v1.5/snapshots/[hash]/model_optimized.onnx

find ~/.local/share/forgetful/models/reranker -name "*.onnx" -o -name tokenizer.json
# ~/.local/share/forgetful/models/reranker/Xenova--ms-marco-MiniLM-L-12-v2/onnx/model.onnx
# ~/.local/share/forgetful/models/reranker/Xenova--ms-marco-MiniLM-L-12-v2/tokenizer.json
```

## Custom Models

To use different embedding models, set:
```bash
EMBEDDING_MODEL=sentence-transformers/all-MiniLM-L6-v2
RERANKING_MODEL=cross-encoder/ms-marco-MiniLM-L-6-v2
```

**Note:** Custom models require HuggingFace access for download. Only the default embedding model (BAAI/bge-small-en-v1.5) is available pre-packaged from GitHub. A custom `RERANKING_MODEL` must be a HuggingFace repository that holds `onnx/model.onnx` and `tokenizer.json`.

## Troubleshooting

### Models not found
If Forgetful can't find the models:
1. Verify the cache directory exists
2. Check file permissions (should be readable by your user)
3. Ensure the directory structure matches the expected format (see Option 2 above)
4. Check that `FASTEMBED_CACHE_DIR` points to the correct location (if using custom path)
5. For the reranker, check `RERANKING_MODEL_DIR` and that the repository directory holds `onnx/model.onnx` and `tokenizer.json`

### Unexpected downloads
If Forgetful downloads models despite having them cached:
- Verify models are in the correct subdirectory structure
- Check logs to see which cache directory is being used
- Ensure you're using the default model names (or have cached your custom models)

## Support

For issues or questions:
- GitHub Issues: https://github.com/scottrbk/forgetful/issues
