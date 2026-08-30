from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Protocol

from wp_modernizer.application.ports import MutableOperations
from wp_modernizer.domain.models import StepResult


class Step(Protocol):
    @property
    def name(self) -> str: ...

    @property
    def mutable(self) -> bool: ...

    @property
    def idempotent(self) -> bool: ...

    @property
    def completion_probe(self) -> str: ...

    @property
    def partial_recovery(self) -> str: ...

    def execute(self, context: Dict[str, Any]) -> StepResult: ...


@dataclass(frozen=True)
class OperationStep:
    name: str
    operations: MutableOperations
    mutable: bool = True
    idempotent: bool = True
    completion_probe: str = "ponto de controle e estado do destino inspecionado"
    partial_recovery: str = "inspecionar e depois repetir ou pausar"

    def execute(self, context: Dict[str, Any]) -> StepResult:
        return self.operations.execute(self.name, context)


UPDATE_STEP_NAMES = (
    "preflight",
    "snapshot",
    "pending_search_replace",
    "core_update",
    "core_database_update",
    "managed_plugin_refresh",
    "third_party_plugin_update",
    "theme_update",
    "core_languages",
    "plugin_languages",
    "theme_languages",
    "widget_validation",
    "final_health_check",
)
