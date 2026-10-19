"""The node and edge counts of a subgraph's metadata."""
from collections import Counter
from itertools import chain

from app.models.graph_models import SubgraphEdge, SubgraphNode
from app.services.graph.edges import EDGE_TYPES
from app.services.graph.node_ids import NODE_TYPES


def count_fields(nodes: list[SubgraphNode], edges: list[SubgraphEdge]) -> dict[str, int]:
    """Count the nodes of each node type and the edges of each edge type.

    Returns the fields of SubgraphMeta named '<type>_count', zero for types that are absent.
    """
    tally = Counter(chain((n.type for n in nodes), (e.type for e in edges)))
    return {f"{kind}_count": tally[kind] for kind in (*NODE_TYPES, *EDGE_TYPES)}
