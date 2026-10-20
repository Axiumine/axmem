"""Tool Adapters - Bridge between services and tool registry

The adapter classes wrap service methods as registry-compatible callables, ensuring the
tenant is properly extracted and preserved. They live per domain in
app.routes.mcp.adapters; this module re-exports them (and the argument helpers) so the
registry, bootstrap and tests keep a single import path.
"""

from app.routes.mcp.adapters import arguments
from app.routes.mcp.adapters.memory import MemoryToolAdapters, create_memory_adapters

# The argument helpers keep their historical private names for existing importers.
_coerce_bool = arguments.coerce_bool
_coerce_int = arguments.coerce_int
_coerce_int_ids = arguments.coerce_int_ids
_coerce_optional_int_id = arguments.coerce_optional_int_id
_coerce_optional_int_ids = arguments.coerce_optional_int_ids
_coerce_str_list = arguments.coerce_str_list
_reject_unknown_args = arguments.reject_unknown_args

__all__ = [
    "MemoryToolAdapters",
    "_coerce_bool",
    "_coerce_int",
    "_coerce_int_ids",
    "_coerce_optional_int_id",
    "_coerce_optional_int_ids",
    "_coerce_str_list",
    "_reject_unknown_args",
    "create_memory_adapters",
]
