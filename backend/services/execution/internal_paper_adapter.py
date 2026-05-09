from __future__ import annotations

from backend.services.execution.desk_adapter import DeskExecutionAdapter


class InternalPaperExecutionAdapter(DeskExecutionAdapter):
    @property
    def adapter_name(self) -> str:
        return "internal_paper"
