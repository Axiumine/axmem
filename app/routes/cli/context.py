"""Context shim for in-process CLI tool execution.

Tool adapters receive a FastMCP Context but read three attributes from it:
ctx.fastmcp.tenant_service, ctx.fastmcp.auth and ctx.fastmcp.trusted_local
(app/middleware/auth.py). auth=None routes get_tenant_from_auth down its local-tenant
branch, and trusted_local lets that branch run whatever AUTH_MODE says: the in-process CLI
has no network input, so it is the operator's own tenant (DEFAULT_TENANT) and cannot name
another one.
"""
from app.bootstrap import Runtime


class _CliRuntime:
    """Stands in for the FastMCP instance attributes that adapters read."""

    def __init__(self, runtime: Runtime):
        self.tenant_service = runtime.services.tenant
        self.auth = None
        self.trusted_local = True


class CliContext:
    """Duck-typed FastMCP Context handed to tool adapters by LocalExecutor."""

    def __init__(self, runtime: Runtime):
        self.fastmcp = _CliRuntime(runtime)
