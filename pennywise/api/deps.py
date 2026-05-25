"""FastAPI dependency factories."""
from __future__ import annotations

from pennywise.api.auth import current_user

# Re-export for convenience — routes import from deps.
__all__ = ["current_user"]
