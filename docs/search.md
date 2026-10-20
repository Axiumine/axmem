# Forgetful Search Functionality

This section details Forgetful's search functionality and associated configuration.

## Auto-Linking Pipeline

When a new memory is created, Forgetful automatically builds knowledge graph connections:

```mermaid
flowchart LR
    A[Create memory] --> B[Generate embedding]
    B --> C[Store memory]
    C --> D[Find nearest memories]
    D --> E{Cosine similarity ≥ threshold?}
    E -- Yes, up to configured limit --> F[Link memories]
    E -- No --> G[Leave unlinked]
```

**How it works:**

1. **Encode** - Title, content, context, keywords, and tags are combined into a vector embedding
2. **Store** - The embedding is stored in the Forgetful database alongside the memory
3. **Similarity Search** - Existing memories are searched for semantic similarity
4. **Threshold** - Only matches with cosine similarity of at least 0.7 are kept by default
5. **Auto-Link** - Up to `MEMORY_NUM_AUTO_LINK` qualifying memories are linked (bidirectional)

This builds a knowledge graph where related concepts connect without manual intervention.

Set `MEMORY_SIMILARITY_THRESHOLD` to adjust the cutoff. Auto-linking does not use
cross-encoder reranking.

## Query Search Pipeline

`query_memory` searches by vector similarity. When reranking is enabled and more
than `k` candidates are returned, a cross-encoder reranks them using `query` and
`query_context`. The query and every memory are embedded on the NVIDIA GPU with TensorRT in strict fp32
by default (see [GPU models](gpu-models.md)), and the local reranker runs there too; `RERANKING_PROVIDER=HTTP` sends the candidates to a rerank service
instead. There is no sparse full-text search or reciprocal rank fusion.

---

## Embedding Providers

### TensorRT (default)
`EMBEDDING_PROVIDER=TensorRT` embeds in the server process on the NVIDIA GPU with `BAAI/bge-small-en-v1.5` (CLS pooling, L2 normalised, 384 dimensions, texts truncated to 512 tokens), in strict fp32. There is no CPU path: a host without a usable GPU refuses to start and says why. See [GPU models](gpu-models.md).

### [Google](https://ai.google.dev/gemini-api/docs/embeddings)
We also support Google Embedding models available via the Gemini API

### [Azure](https://learn.microsoft.com/en-us/azure/ai-foundry/openai/tutorials/embeddings?view=foundry-classic&tabs=command-line)
Support for the Azure Foundry OpenAI embeddings is now added as well. 

### Gateway
An optional HTTP embeddings gateway (`EMBEDDING_PROVIDER=gateway`). Texts go out in batches of up to 256 and the returned vectors are checked against `EMBEDDING_DIMENSIONS` before they are stored. See the [gateway settings](configuration.md#gateway-embedding-provider-configuration).
## Configuration
The following configuration options are available for search
