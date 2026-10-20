"""Texts whose cosine similarity under the production embedder the tests rely on.

The real TensorRT embedder (BAAI/bge-small-en-v1.5, strict fp32) gives these values for the embedding
text of a memory (title, content, context, keywords and tags joined by spaces), measured on the
reference host. ``tests/integration/test_embedding_texts.py`` pins them, so a change of model or
of numerics fails there and not as a puzzling failure of the tests that use the texts.

| Pair | Cosine | Used to show |
|---|---|---|
| FEEDING to FEEDING (other number) | 0.99 | near-duplicates |
| FEEDING to BAKING | 0.83 | clearly similar: above the 0.7 auto-link threshold, below the 0.89 obsolete threshold |
| FEEDING to POOL, BAKING to POOL | 0.46, 0.37 | clearly unrelated: far below the 0.7 auto-link threshold |
| FEEDING_QUERY to FEEDING, BAKING | 0.88, 0.78 | the query finds the FEEDING memories first |
"""

FEEDING_TITLE = "Sourdough starter feeding"
FEEDING_CONTENT = "Feed the sourdough starter equal parts flour and water every twelve hours so the wild yeast stays active."
FEEDING_CONTEXT = "bread baking"

BAKING_TITLE = "Baking with sourdough starter"
BAKING_CONTENT = (
    "Bake the loaf with an active sourdough starter: shape the dough, proof it overnight in the fridge "
    "and bake it in a hot dutch oven."
)
BAKING_CONTEXT = "bread baking"

POOL_TITLE = "Postgres pool sizing"
POOL_CONTENT = "Set the connection pool size to twice the number of cores for an async database client."
POOL_CONTEXT = "database tuning"

FEEDING_QUERY = "How often should the sourdough starter be fed with flour and water"
FEEDING_QUERY_CONTEXT = "looking up bread notes"
