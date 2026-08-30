from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, FrozenSet, List, Optional, Tuple

from .enums import (
    Capability,
    Environment,
    HealthStatus,
    Operation,
    PendingOperationType,
    RunStatus,
    StepStatus,
)
from .errors import UnsafeOperationError


@dataclass(frozen=True)
class WordPressInstallation:
    installation_id: str
    path: Path
    app_root: Path
    domain: str
    instance_name: str
    document_root: Path
    environment: Environment
    relative_nested_path: Optional[Path] = None
    parent_installation: Optional[str] = None
    children: Tuple[str, ...] = ()


@dataclass(frozen=True)
class MigrationTarget:
    installation_id: str
    path: Path
    environment: Environment

    def __post_init__(self) -> None:
        if self.environment is not Environment.TEST:
            raise UnsafeOperationError("O destino automatizado deve ser de TESTE")


@dataclass(frozen=True)
class ProbeResult:
    capability: Capability
    available: bool
    detail: str = ""


@dataclass(frozen=True)
class CapabilityReport:
    results: Tuple[ProbeResult, ...]
    health: HealthStatus
    fatal_errors: Tuple[str, ...] = ()

    @property
    def available(self) -> FrozenSet[Capability]:
        return frozenset(item.capability for item in self.results if item.available)

    def has(self, capability: Capability) -> bool:
        return capability in self.available


@dataclass(frozen=True)
class PendingOperation:
    operation_type: PendingOperationType
    parameters: Dict[str, str]
    reason: str
    completed: bool = False


@dataclass(frozen=True)
class PlannedStep:
    name: str
    mutable: bool
    idempotent: bool
    completion_probe: str
    partial_recovery: str
    installation_id: str
    excludes: Tuple[Path, ...] = ()


@dataclass(frozen=True)
class MigrationPlan:
    installation_id: str
    source_environment: Environment
    destination_environment: Environment
    source_server: str
    database_endpoint: Optional[str]
    installations: Tuple[WordPressInstallation, ...]
    steps: Tuple[PlannedStep, ...]
    pending_operations: Tuple[PendingOperation, ...] = ()

    def __post_init__(self) -> None:
        if self.destination_environment is not Environment.TEST:
            raise UnsafeOperationError("Planos de migração só podem ter TESTE como destino")


@dataclass(frozen=True)
class StepResult:
    name: str
    status: StepStatus
    changed: bool
    message: str
    metrics: Dict[str, float] = field(default_factory=dict)
    installation_id: Optional[str] = None


@dataclass
class RunManifest:
    run_id: str
    installation_id: str
    operation: Operation
    status: RunStatus
    started_at: str
    dry_run: bool
    steps: List[StepResult] = field(default_factory=list)
    pending_operations: List[PendingOperation] = field(default_factory=list)
    last_successful_step: Optional[str] = None
    failed_step: Optional[str] = None
    health_before: Optional[HealthStatus] = None
    health_after: Optional[HealthStatus] = None
    wpcli_full_bootstrap: bool = False
    wpcli_reduced_bootstrap: bool = False
    fatal_errors: List[str] = field(default_factory=list)
    widget_diff: List[Dict[str, str]] = field(default_factory=list)
    filesystem_fingerprint: Optional[str] = None
    finished_at: Optional[str] = None
    planned_steps: List[PlannedStep] = field(default_factory=list)
    migration_plan: Optional[MigrationPlan] = None
