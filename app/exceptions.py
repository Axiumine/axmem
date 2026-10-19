"""Domain exceptions for the application
"""

class NotFoundError(Exception):
    """Raised when a requested resource is not found"""


class AssociationNotFoundError(NotFoundError):
    """Raised when a resource referenced by id in a request (not the target itself) is not found"""

class ConflictError(Exception):
    """Raised when there's a resource conflict (e.g., duplicate key)"""


class InvalidStateTransitionError(Exception):
    """Raised when a state machine transition is not allowed"""
