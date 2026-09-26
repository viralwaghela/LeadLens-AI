"""Public-demo usage counters.

One tiny table so the demo's LLM caps (hourly and daily) are GLOBAL across
every visitor and survive a restart — an in-process counter would reset on
each reboot and could not span sessions. It is the only table the running
demo app is allowed to write to (see core/db/demo_guard.py's
ALLOWED_WRITE_TABLES).

Dormant everywhere except a deployment with LEADLENS_DEMO_MODE set; on any
other deployment the table simply stays empty.
"""
from __future__ import annotations

from sqlalchemy import Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from core.db.base import Base


class DemoUsageCounter(Base):
    __tablename__ = "demo_usage"
    __table_args__ = (
        UniqueConstraint("window_key", "scope", name="uq_demo_usage_window_key_scope"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # "2026-09-26" for a daily window, "2026-09-26T14" for an hourly one.
    window_key: Mapped[str] = mapped_column(String(20), nullable=False)
    # What is being counted, e.g. "llm_calls".
    scope: Mapped[str] = mapped_column(String(60), nullable=False)
    count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
