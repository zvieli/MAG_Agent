"""Authentication helpers for Azure Easy Auth identity and scope authorization."""

from src.auth.decorators import require_authenticated_scope
from src.auth.easy_auth import (
    PrincipalAuthenticationError,
    get_authenticated_principal,
    has_required_scope,
)

__all__ = [
    "PrincipalAuthenticationError",
    "get_authenticated_principal",
    "has_required_scope",
    "require_authenticated_scope",
]
