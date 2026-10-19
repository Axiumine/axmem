"""Scoped Permissions for Meta-Tools

Resolves OAuth-style scopes to sets of permitted tool names.
Two-layer model:
  1. Instance-level ceiling via FORGETFUL_SCOPES env var (default "*")
  2. Per-session restriction via OAuth token `scope`/`scp` claim (intersection model)

Scope tokens:
  *                -> all tools
  read             -> all tools where mutates=False
  write            -> all tools where mutates=True
  read:<category>  -> category read tools only
  write:<category> -> category write tools only

Categories: users, memories, projects, entities
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

from app.config.logging_config import logging
from app.models.tool_registry_models import ToolCategory, ToolMetadata
from app.routes.server_state import server_of

if TYPE_CHECKING:
    from fastmcp import Context

    from app.routes.mcp.tool_registry import ToolRegistry

logger = logging.getLogger(__name__)

# Map ToolCategory enum values to scope category names (plural)
CATEGORY_TO_SCOPE: dict[str, str] = {
    ToolCategory.USER.value: "users",
    ToolCategory.MEMORY.value: "memories",
    ToolCategory.PROJECT.value: "projects",
    ToolCategory.ENTITY.value: "entities",
    ToolCategory.LINKING.value: "entities",  # linking tools are entity-scoped
}

# Reverse map: scope category -> ToolCategory enum values
_SCOPE_TO_CATEGORIES: dict[str, list[str]] = {}
for _cat_value, _scope_name in CATEGORY_TO_SCOPE.items():
    _SCOPE_TO_CATEGORIES.setdefault(_scope_name, []).append(_cat_value)

# Token claims that can carry the OAuth scopes, in precedence order (as in FastMCP's JWTVerifier)
_TOKEN_SCOPE_CLAIMS = ("scope", "scp")

VALID_ACTIONS = frozenset({"read", "write"})
VALID_CATEGORIES = frozenset(_SCOPE_TO_CATEGORIES.keys())


def _validate_scope_token(token: str) -> None:
    """Check one scope token against the accepted formats

    Raises:
        ValueError: If the token is not ``*``, ``read``, ``write`` or ``<action>:<category>``
    """
    if token == "*" or token in VALID_ACTIONS:  # nosec B105 - scope grant name, not a credential
        # "*" is every tool, a bare "read" or "write" applies to all categories
        return

    if ":" not in token:
        raise ValueError(
            f"Invalid scope token '{token}'. "
            f"Valid formats: *, read, write, read:<category>, write:<category>",
        )

    action, category = token.split(":", 1)
    if action not in VALID_ACTIONS:
        raise ValueError(
            f"Invalid scope action '{action}' in '{token}'. "
            f"Valid actions: {', '.join(sorted(VALID_ACTIONS))}",
        )
    if category not in VALID_CATEGORIES:
        raise ValueError(
            f"Invalid scope category '{category}' in '{token}'. "
            f"Valid categories: {', '.join(sorted(VALID_CATEGORIES))}",
        )


def parse_scopes(scope_string: str) -> frozenset[str]:
    """Parse and validate a comma-separated scope string.

    Args:
        scope_string: Comma-separated scopes (e.g. "*", "read", "read,write:memories")

    Returns:
        Frozen set of validated scope tokens

    Raises:
        ValueError: If any scope token is invalid
    """
    raw = scope_string.strip()
    if not raw:
        raise ValueError("Scope string cannot be empty")

    tokens = frozenset(t.strip() for t in raw.split(",") if t.strip())
    if not tokens:
        raise ValueError("Scope string cannot be empty")

    for token in tokens:
        _validate_scope_token(token)

    return tokens


def _token_permits(token: str, tool: ToolMetadata) -> bool:
    """Whether one validated scope token (other than ``*``) permits a tool"""
    if ":" in token:
        action, scope_category = token.split(":", 1)
        if tool.category.value not in _SCOPE_TO_CATEGORIES.get(scope_category, []):
            return False
        return tool.mutates == (action != "read")
    if token == "read":  # nosec B105 - scope grant name, not a credential
        return not tool.mutates
    if token == "write":  # nosec B105 - scope grant name, not a credential
        return tool.mutates
    return False


def resolve_permitted_tools(scopes: frozenset[str], registry: ToolRegistry) -> set[str]:
    """Resolve scope tokens to a set of permitted tool names.

    Args:
        scopes: Validated scope tokens from parse_scopes()
        registry: ToolRegistry with registered tools

    Returns:
        Set of tool names permitted by the scopes
    """
    if "*" in scopes:
        return {m.name for m in registry.list_all_tools()}

    return {
        tool.name
        for tool in registry.list_all_tools()
        for token in scopes
        if _token_permits(token, tool)
    }


def get_required_scope(tool_name: str, registry: ToolRegistry) -> str:
    """Get the scope required to access a specific tool (for error messages).

    Args:
        tool_name: Name of the tool
        registry: ToolRegistry instance

    Returns:
        Scope string like "write:memories" or "read:users"
    """
    tool = registry.get_tool(tool_name)
    if not tool:
        return "unknown"

    action = "write" if tool.metadata.mutates else "read"
    scope_category = CATEGORY_TO_SCOPE.get(tool.metadata.category.value, "unknown")
    return f"{action}:{scope_category}"


def get_effective_scopes(ctx: Context) -> tuple[set[str], frozenset[str]]:
    """Resolve effective permitted tools for the current request.

    Intersection model:
    1. Start with instance-level scopes from ctx.fastmcp._instance_permitted_tools
    2. If OAuth token has scopes, intersect with token-level permissions
       (a token whose scopes include no Forgetful scope permits nothing)
    3. If no token scopes, fall back to instance-level permissions

    Args:
        ctx: FastMCP Context

    Returns:
        Tuple of (permitted_tool_names, effective_scope_tokens)
    """
    server = server_of(ctx)
    instance_permitted: set[str] = getattr(server, "_instance_permitted_tools", None) or set()
    instance_scopes: frozenset[str] = getattr(server, "_instance_scopes", frozenset({"*"}))

    if not instance_permitted:
        # Fallback: if not initialized, allow all (backwards compatible)
        all_tools = {m.name for m in server.registry.list_all_tools()}
        return all_tools, frozenset({"*"})

    # Check for OAuth token scopes
    token_scopes = _extract_token_scopes(ctx)

    if token_scopes is None:
        # No auth or no scope claim — fall back to instance ceiling
        return set(instance_permitted), instance_scopes

    # Resolve token scopes to tool names and intersect with instance ceiling
    registry: ToolRegistry = server.registry
    token_permitted = resolve_permitted_tools(token_scopes, registry)
    effective_permitted = instance_permitted & token_permitted
    effective_scopes = instance_scopes | token_scopes  # Union for display

    logger.debug(
        f"Scope intersection: instance={len(instance_permitted)} tools, "
        f"token={len(token_permitted)} tools, effective={len(effective_permitted)} tools",
    )

    return effective_permitted, effective_scopes


def _current_access_token() -> Any:
    """The OAuth access token of the current request, or None when there is none"""
    try:
        from fastmcp.server.dependencies import get_access_token
        return get_access_token()
    except Exception:
        return None


def _scopes_from_claim(claim: Any) -> list[str]:
    """Split one token claim into raw scope strings"""
    if isinstance(claim, str):
        return claim.split()
    if isinstance(claim, list | tuple):
        return " ".join(str(item) for item in claim).split()
    if claim:
        # Not a type that can carry scopes, but the token was meant to be limited
        return [str(claim)]
    return []


def _raw_token_scopes(claims: dict[str, Any]) -> list[str]:
    """Raw scope strings from the first scope claim that carries any"""
    for claim_name in _TOKEN_SCOPE_CLAIMS:
        raw_scopes = _scopes_from_claim(claims.get(claim_name))
        if raw_scopes:
            return raw_scopes
    return []


def _split_recognised_scopes(raw_scopes: list[str]) -> tuple[set[str], list[str]]:
    """Separate the Forgetful scopes from the ones this module does not understand"""
    recognised: set[str] = set()
    ignored: list[str] = []
    for raw_scope in raw_scopes:
        try:
            recognised |= parse_scopes(raw_scope)
        except ValueError:
            ignored.append(raw_scope)
    return recognised, ignored


def _extract_token_scopes(ctx: Context) -> frozenset[str] | None:
    """Extract scopes from OAuth access token if present.

    Returns None if no auth is configured or token has no scope claim, in which
    case the instance ceiling applies unchanged.

    The scopes are read from the `scope` claim, then the `scp` claim (the same
    precedence as FastMCP's JWTVerifier). Each claim may be a space-separated
    string (RFC 6749) or a list. Tokens that are not Forgetful scopes (for example
    `openid` or another API's scopes) are ignored one by one, so they never drop
    the Forgetful scopes next to them.

    A token that carries scopes but none of them is a Forgetful scope gets an
    empty set (nothing permitted): a token must never widen to the full instance
    ceiling because of scopes this module does not understand.
    """
    token = _current_access_token()
    if token is None:
        return None

    raw_scopes = _raw_token_scopes(token.claims)
    if not raw_scopes:
        return None

    recognised, ignored = _split_recognised_scopes(raw_scopes)

    if not recognised:
        logger.warning(
            f"OAuth token scopes {ignored} contain no recognised Forgetful scope, denying all tools",
        )
        return frozenset()

    if ignored:
        logger.debug(f"Ignoring unrecognised OAuth token scopes: {ignored}")

    return frozenset(recognised)
