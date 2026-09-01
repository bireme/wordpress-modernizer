from __future__ import annotations

from typing import Any, Dict, Protocol

from wp_modernizer.application.ports import MutableOperations
from wp_modernizer.domain.models import PlannedStep, StepResult


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

    @property
    def installation_id(self) -> str: ...

    def execute(self, context: Dict[str, Any]) -> StepResult: ...


class OperationStep:
    """Executable wrapper that keeps the domain plan attached to the operation.

    Accepting a name remains supported for small adapters and older tests, but service code
    always supplies a complete PlannedStep.
    """

    def __init__(self, planned_step: PlannedStep | str, operations: MutableOperations) -> None:
        if isinstance(planned_step, str):
            planned_step = planned_update_step(planned_step, "")
        self.planned_step = planned_step
        self.operations = operations

    @property
    def name(self) -> str:
        return self.planned_step.name

    @property
    def mutable(self) -> bool:
        return self.planned_step.mutable

    @property
    def idempotent(self) -> bool:
        return self.planned_step.idempotent

    @property
    def completion_probe(self) -> str:
        return self.planned_step.completion_probe

    @property
    def partial_recovery(self) -> str:
        return self.planned_step.partial_recovery

    @property
    def installation_id(self) -> str:
        return self.planned_step.installation_id

    def execute(self, context: Dict[str, Any]) -> StepResult:
        step_context = dict(context)
        step_context["planned_step"] = self.planned_step
        return self.operations.execute(self.name, step_context)


def planned_update_step(name: str, installation_id: str) -> PlannedStep:
    return PlannedStep(
        name=name,
        mutable=True,
        idempotent=True,
        completion_probe="ponto de controle e estado do destino inspecionado",
        partial_recovery="inspecionar e depois repetir ou pausar",
        installation_id=installation_id,
    )


def planned_update_steps(installation_id: str) -> tuple[PlannedStep, ...]:
    return tuple(planned_update_step(name, installation_id) for name in UPDATE_STEP_NAMES)


UPDATE_STEP_NAMES = (
    "preflight",
    "pending_search_replace",
    "snapshot",
    "core_update",
    "core_database_update",
    "managed_plugin_refresh",
    "third_party_plugin_update",
    "theme_update",
    "core_languages",
    "plugin_languages",
    "theme_languages",
    "widget_validation",
)
