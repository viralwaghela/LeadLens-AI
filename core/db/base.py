"""Declarative base for the V2 relational schema.

One shared Base so every model in core/db/models/ registers onto the same
metadata, which is what alembic/env.py points at for autogenerate/drift
checks. A naming convention is set explicitly so Alembic generates stable,
predictable constraint/index names across autogenerate runs instead of
SQLAlchemy's default anonymous names, which change between runs and would
otherwise make every autogenerate look like a diff even when nothing
changed.
"""
from __future__ import annotations

from sqlalchemy import MetaData
from sqlalchemy.orm import DeclarativeBase

NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)
