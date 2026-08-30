from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Mapping, Optional, Protocol, Sequence, Set, Tuple

from wp_modernizer.domain.models import CapabilityReport, RunManifest, StepResult
from wp_modernizer.domain.widgets import WidgetSnapshot


class SecretProvider(Protocol):
    def get(self, reference: str) -> str: ...


class ServerRegistry(Protocol):
    def get_server(self, server_id: str) -> Any: ...


class DatabaseRegistry(Protocol):
    def get_database(self, endpoint_id: str) -> Any: ...

    def list_schemas(self, endpoint_id: str) -> Set[str]: ...


@dataclass(frozen=True)
class CommandResult:
    argv: Tuple[str, ...]
    return_code: int
    stdout: str
    stderr: str
    elapsed_seconds: float
    correlation_id: Optional[str]


class CommandRunner(Protocol):
    def run(
        self,
        argv: Sequence[str],
        *,
        cwd: Optional[Path] = None,
        timeout: float = 60,
        environment: Optional[Mapping[str, str]] = None,
        stdin_path: Optional[Path] = None,
        stdout_path: Optional[Path] = None,
        correlation_id: Optional[str] = None,
    ) -> CommandResult: ...


class FileSystem(Protocol):
    def exists(self, path: Path) -> bool: ...

    def read_text(self, path: Path) -> str: ...

    def fingerprint(self, path: Path) -> str: ...

    def remove_tree(self, path: Path) -> None: ...


class CapabilityProbePort(Protocol):
    def probe(self, installation_path: Path) -> CapabilityReport: ...


class WidgetStore(Protocol):
    def snapshot(self, installation_id: str) -> WidgetSnapshot: ...

    def restore(self, installation_id: str, snapshot: WidgetSnapshot) -> None: ...


class StateStore(Protocol):
    def create_run(self, manifest: RunManifest) -> None: ...

    def save_manifest(self, manifest: RunManifest) -> None: ...

    def load_manifest(self, installation_id: str, run_id: str) -> RunManifest: ...

    def save_checkpoint(
        self, installation_id: str, run_id: str, step: StepResult, health: CapabilityReport
    ) -> None: ...


class Clock(Protocol):
    def now_iso(self) -> str: ...


class IdGenerator(Protocol):
    def new(self) -> str: ...


class MutableOperations(Protocol):
    def execute(self, step_name: str, context: Dict[str, Any]) -> StepResult: ...
