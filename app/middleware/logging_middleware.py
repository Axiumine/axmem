"""Context variable for logging - request/operation tracking.

This module provides:

- contextVar storage for request_id, user_id and operation context
- Helper functions to read the context values (the variables themselves are set directly)
"""

from contextvars import ContextVar

request_id_var: ContextVar[str | None] = ContextVar("request_id", default=None)
user_id_var: ContextVar[str | None] = ContextVar("user_id", default=None)

def get_request_id() -> str | None:
    """Get the current requst/operation ID from context."""
    return request_id_var.get()

def get_user_id() -> str | None:
    """Get the current user id from context"""
    return user_id_var.get()
