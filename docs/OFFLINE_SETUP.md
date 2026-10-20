# HuggingFace-Free Setup Guide

This guide explains how to use Forgetful in environments where HuggingFace is blocked by corporate firewalls or network restrictions.

## Use Case

Forgetful uses FastEmbed for local embeddings, which normally auto-downloads models from HuggingFace on first run. In corporate environments where HuggingFace is blocked but PyPI and GitHub are accessible, you can pre-download models from GitHub instead.

**What works normally:**
- Installing forgetful: `uvx forgetful-ai` or `pip install forgetful-ai`
- Package updates from PyPI

**What needs pre-caching:**
- Embedding and reranking models (normally from HuggingFace, pre-packaged on GitHub)

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

2. **Reranking Model**: `Xenova/ms-marco-MiniLM-L-12-v2` (~129MB)
   - Used for reranking search results by relevance

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
├── models--Xenova--ms-marco-MiniLM-L-12-v2/
│   └── snapshots/
│       └── [hash]/
│           ├── config.json
│           ├── model.onnx
│           └── tokenizer files...
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

In this mode, both the embedding model and FastEmbed reranker must already be
present under `FASTEMBED_CACHE_DIR`. If not, Forgetful exits with an error that
points to the cache directory and configured model names.

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

## Startup without internet

Install Forgetful and its Python dependencies while internet is available (`uv sync` from a
checkout, or `uvx forgetful-ai`). Starting from an already synced checkout with
`uv run --no-sync main.py` does not contact PyPI or rebuild the package.

Embedding and reranking models are downloaded separately. Memory queries also use
tiktoken's token-counting data, which uses a separate cache (`TIKTOKEN_CACHE_DIR`, read by
tiktoken itself). Without it, queries still work but budget tokens with an approximate
character-based estimate. Keep both caches on persistent storage, for example:

```dotenv
FASTEMBED_CACHE_DIR=/opt/models/fastembed
TIKTOKEN_CACHE_DIR=/opt/models/tiktoken
```

Normal startup downloads the configured FastEmbed models. While internet is available,
populate the token-counting cache too:

```bash
uv run python -c 'from app.utils.token_counter import TokenCounter; TokenCounter()'
```

After preparation, set `FASTEMBED_LOCAL_FILES_ONLY=true` to require the cached models.
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
# ~/.local/share/forgetful/models/fastembed/models--Xenova--ms-marco-MiniLM-L-12-v2/snapshots/[hash]/model.onnx
```

## Custom Models

To use different embedding models, set:
```bash
EMBEDDING_MODEL=sentence-transformers/all-MiniLM-L6-v2
RERANKING_MODEL=cross-encoder/ms-marco-MiniLM-L-6-v2
```

**Note:** Custom models require HuggingFace access for download. Only the default models (BAAI/bge-small-en-v1.5, Xenova/ms-marco-MiniLM-L-12-v2) are available pre-packaged from GitHub.

## Troubleshooting

### Models not found
If Forgetful can't find the models:
1. Verify the cache directory exists
2. Check file permissions (should be readable by your user)
3. Ensure the directory structure matches the expected format (see Option 2 above)
4. Check that `FASTEMBED_CACHE_DIR` points to the correct location (if using custom path)

### Unexpected downloads
If Forgetful downloads models despite having them cached:
- Verify models are in the correct subdirectory structure
- Check logs to see which cache directory is being used
- Ensure you're using the default model names (or have cached your custom models)

## Support

For issues or questions:
- GitHub Issues: https://github.com/scottrbk/forgetful/issues
