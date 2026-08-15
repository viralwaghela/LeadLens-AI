"""Import every model module so Base.metadata is fully populated.

alembic/env.py imports this package (not the individual model modules)
so autogenerate/drift-check sees every table Phase 0 defines. Add new
model modules' imports here when they're created, or Alembic will
silently not know about them.
"""
from __future__ import annotations

from core.db.models import clinic  # noqa: F401
from core.db.models import identity  # noqa: F401
from core.db.models import jarvis  # noqa: F401
from core.db.models import operations  # noqa: F401
from core.db.models import organization  # noqa: F401

__all__ = ["clinic", "identity", "jarvis", "operations", "organization"]
