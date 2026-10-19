"""Node types and the 'type_id' node id format of a subgraph."""
import re
from typing import Literal, get_args

NodeType = Literal[
    "memory", "entity", "project", "document", "code_artifact", "file", "skill", "plan", "task",
]

# Every node type, in the order nodes are listed in a subgraph response.
NODE_TYPES: tuple[NodeType, ...] = get_args(NodeType)

# Regex pattern for parsing node IDs. Matched with fullmatch(): ASCII digits only, so a
# trailing newline or non-ASCII decimal digits are not valid ids.
NODE_ID_PATTERN = re.compile("(" + "|".join(NODE_TYPES) + ")_([0-9]+)")

# Largest id a node_id may carry: object ids are signed 32-bit INTEGER columns on PostgreSQL,
# and drivers raise on a larger value instead of matching no row.
MAX_NODE_ID = 2**31 - 1


def parse_node_id(node_id: str) -> tuple[str, int]:
    """Parse node_id string into type and numeric ID.

    Args:
        node_id: Format 'memory_123', 'entity_456', 'project_789',
                 'document_123', 'code_artifact_456', or 'skill_789'

    Returns:
        Tuple of (node_type, numeric_id)

    Raises:
        ValueError: If node_id format is invalid or the id is out of range
    """
    match = NODE_ID_PATTERN.fullmatch(node_id)
    if not match:
        raise ValueError(
            f"Invalid node_id format: '{node_id}'. "
            "Expected 'memory_{id}', 'entity_{id}', 'project_{id}', "
            "'document_{id}', 'code_artifact_{id}', 'file_{id}', 'skill_{id}', "
            "'plan_{id}', or 'task_{id}'.",
        )
    numeric_id = int(match.group(2))
    if numeric_id > MAX_NODE_ID:
        raise ValueError(
            f"Invalid node_id: '{node_id}' is out of range (maximum id is {MAX_NODE_ID}).",
        )
    return match.group(1), numeric_id
