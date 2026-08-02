from __future__ import annotations

from dataclasses import dataclass, asdict
from datetime import datetime
from typing import Any
from uuid import uuid4


@dataclass
class IntegrationResult:
    provider: str
    action: str
    success: bool
    status: str
    external_id: str = ""
    detail: str = ""
    payload: dict[str, Any] | None = None
    created_at: str = ""

    def __post_init__(self) -> None:
        if not self.created_at:
            self.created_at = datetime.now().isoformat(timespec="seconds")
        if not self.external_id and self.success:
            self.external_id = f"LOCAL-{uuid4().hex[:12].upper()}"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
