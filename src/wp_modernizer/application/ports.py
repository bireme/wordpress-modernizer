from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Mapping, Optional, Protocol, Sequence, Set, Tuple

from wp_modernizer.domain.enums import Capability
from wp_modernizer.domain.models import (
    CapabilityReport,
    DatabaseProbeResult,
    ManagedPlugin,
    ManagedPluginResult,
    RunManifest,
    SourceDatabaseConfiguration,
    SourceDatabaseConnection,
    StepResult,
    WordPressInstallation,
)
from wp_modernizer.domain.widgets import WidgetSnapshot


class SecretProvider(Protocol):
    def get(self, reference: str) -> str: ...


class ServerRegistry(Protocol):
    def get_server(self, server_id: str) -> Any: ...


class DatabaseRegistry(Protocol):
    def get_database(self, endpoint_id: str) -> Any: ...

    def list_schemas(self, endpoint_id: str) -> Set[str]: ...


class DatabaseProbePort(Protocol):
    def probe_database(self, endpoint_id: str, database: str) -> DatabaseProbeResult: ...


class FileTransferPort(ServerRegistry, Protocol):
    def copy_from(
        self,
        server_id: str,
        source: Path,
        destination_parent: Path,
        excludes: Sequence[Path],
        run_id: str,
    ) -> int: ...


class SourceInspectionPort(ServerRegistry, Protocol):
    """Semantically restricted, read-only inspection of a source installation."""

    def inspect_config(
        self, server_id: str, path: Path, run_id: str
    ) -> SourceDatabaseConfiguration: ...


class DatabasePort(DatabaseRegistry, Protocol):
    def dump(self, endpoint_id: str, database: str, output: Path, run_id: str) -> None: ...

    def probe_source(self, connection: SourceDatabaseConnection) -> DatabaseProbeResult: ...

    def dump_source(
        self, connection: SourceDatabaseConnection, output: Path, run_id: str
    ) -> None: ...

    def import_dump(self, endpoint_id: str, database: str, source: Path, run_id: str) -> None: ...

    def snapshot_widgets(self, endpoint_id: str, database: str) -> WidgetSnapshot: ...

    def restore_widgets(
        self, endpoint_id: str, database: str, snapshot: WidgetSnapshot, run_id: str
    ) -> None: ...

    def wordpress_configuration(self, endpoint_id: str, database: str) -> Mapping[str, str]: ...

    def read_site_url(self, endpoint_id: str, database: str, table_prefix: str) -> str: ...

    def read_source_site_url(self, connection: SourceDatabaseConnection) -> str: ...


class WordPressPort(Protocol):
    def get_site_url(self, path: Path, run_id: str) -> str: ...

    def is_multisite(self, path: Path, run_id: str) -> bool: ...

    def get_config(self, path: Path, name: str, run_id: str) -> str: ...

    def search_replace(
        self,
        path: Path,
        old_url: str,
        new_url: str,
        *,
        dry_run: bool,
        multisite: bool,
        run_id: str,
    ) -> int: ...

    def update(self, path: Path, arguments: Sequence[str], run_id: str) -> str: ...


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


class ExecutableLocator(Protocol):
    def which(self, executable: str) -> Optional[str]: ...


class FileSystem(Protocol):
    def exists(self, path: Path) -> bool: ...

    def read_text(self, path: Path) -> str: ...

    def fingerprint(self, path: Path) -> str: ...

    def remove_tree(self, path: Path) -> None: ...

    def is_symlink(self, path: Path) -> bool: ...

    def create_temporary_directory(self, parent: Path, prefix: str) -> Path: ...

    def move(self, source: Path, destination: Path) -> None: ...

    def create_immutable_backup(self, source: Path, destination: Path) -> str: ...

    def verify_backup(self, path: Path, fingerprint: str) -> bool: ...


class ManagedPluginPort(Protocol):
    def refresh(
        self,
        installation: WordPressInstallation,
        plugins: Sequence[ManagedPlugin],
        run_id: str,
    ) -> Tuple[ManagedPluginResult, ...]: ...


class CapabilityProbePort(Protocol):
    def probe(
        self,
        installation_path: Path,
        required_capabilities: Set[Capability] | None = None,
    ) -> CapabilityReport: ...


class WidgetStore(Protocol):
    def snapshot(self, installation_id: str) -> WidgetSnapshot: ...

    def restore(self, installation_id: str, snapshot: WidgetSnapshot) -> None: ...


class StateStore(Protocol):
    def preflight(self) -> None: ...

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

    def validate(self, step_name: str, context: Dict[str, Any]) -> StepResult: ...


class WordPressConfigWriterPort(Protocol):
    def set_config(self, path: Path, values: Mapping[str, str], run_id: str) -> None: ...
