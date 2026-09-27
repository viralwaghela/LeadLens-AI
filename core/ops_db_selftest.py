"""Operator self-test: read-only inspection of the jarvis_learning_records table and
its record_type enum, run from inside this deployment against its own configured
database. Diagnosing a real production error (services/jarvis_memory.py::_load_from_db()
raising psycopg.errors.InvalidTextRepresentation, then falling back to the legacy JSON
file) without needing direct database credentials outside the running app.

Never triggered automatically and shown nowhere by default — see
core/ops_diagnostics.py, gated the same way as core/ops_llm_selftest.py. Strictly
read-only: SELECT only, against pg_catalog/information_schema and one real
JarvisLearningRecord query identical to the one that's failing in production — never
writes, never returns a payload's contents (patient/business data), only counts,
schema metadata, and exception type/message.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class DbSelfTestResult:
    dialect: str = ""
    table_exists: bool | None = None
    enum_type_name: str = ""
    enum_labels_in_db: list[str] = field(default_factory=list)
    enum_labels_in_code: list[str] = field(default_factory=list)
    labels_match: bool | None = None
    query_ok: bool | None = None
    query_error_type: str = ""
    query_error_message: str = ""
    row_count: int | None = None
    detail: str = ""


def run_db_selftest() -> DbSelfTestResult:
    from core.db.session import make_engine, session_scope

    try:
        engine = make_engine()
    except Exception as error:  # noqa: BLE001
        return DbSelfTestResult(detail=f"Could not create a database engine: {type(error).__name__}: {error}")

    dialect = engine.dialect.name
    if dialect != "postgresql":
        return DbSelfTestResult(dialect=dialect, detail=f"Not Postgres ({dialect}) — the enum check only applies there.")

    from sqlalchemy import text

    from core.db.models.jarvis import JarvisLearningRecordType

    code_labels = [member.name for member in JarvisLearningRecordType]
    enum_type_name = "jarvis_learning_record_type"

    try:
        with session_scope(engine) as session:
            table_exists = bool(session.execute(
                text("SELECT to_regclass('public.jarvis_learning_records') IS NOT NULL")
            ).scalar())

            db_labels = [
                row[0] for row in session.execute(
                    text(
                        "SELECT enumlabel FROM pg_enum "
                        "JOIN pg_type ON pg_enum.enumtypid = pg_type.oid "
                        "WHERE pg_type.typname = :type_name "
                        "ORDER BY pg_enum.enumsortorder"
                    ),
                    {"type_name": enum_type_name},
                ).all()
            ]

            result = DbSelfTestResult(
                dialect=dialect,
                table_exists=table_exists,
                enum_type_name=enum_type_name,
                enum_labels_in_db=db_labels,
                enum_labels_in_code=code_labels,
                labels_match=(set(db_labels) == set(code_labels)),
            )

            if not table_exists:
                return DbSelfTestResult(**{**result.__dict__, "detail": "Table does not exist in this database."})

            # The exact query pattern services/jarvis_memory.py::_load_from_db() runs —
            # reproduces the real failure (or its absence) directly, read-only, LIMIT 1.
            try:
                from core.db.models.jarvis import JarvisLearningRecord

                count = session.query(JarvisLearningRecord).filter(
                    JarvisLearningRecord.record_type == JarvisLearningRecordType.PREFERENCE
                ).limit(1).count()
                return DbSelfTestResult(**{**result.__dict__, "query_ok": True, "row_count": count})
            except Exception as query_error:  # noqa: BLE001 - report, never crash the panel
                session.rollback()
                return DbSelfTestResult(**{
                    **result.__dict__,
                    "query_ok": False,
                    "query_error_type": type(query_error).__name__,
                    "query_error_message": str(query_error)[:500],
                })
    except Exception as error:  # noqa: BLE001
        return DbSelfTestResult(dialect=dialect, detail=f"{type(error).__name__}: {error}"[:500])
