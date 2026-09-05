from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from wp_modernizer.domain.enums import Capability, HealthStatus, StepCapability, StepStatus
from wp_modernizer.domain.models import CapabilityReport, ProbeResult, RunManifest, StepResult


@dataclass
class FakeCommandResult:
    return_code: int = 0
    stdout: str = ""
    stderr: str = ""
    elapsed_seconds: float = 0.01


class FakeCommandRunner:
    def __init__(self, results: Optional[List[FakeCommandResult]] = None) -> None:
        self.results = results or []
        self.calls: List[Sequence[str]] = []

    def run(self, argv: Sequence[str], **kwargs: Any) -> FakeCommandResult:
        self.calls.append(tuple(argv))
        result = self.results.pop(0) if self.results else FakeCommandResult()
        if kwargs.get("stdout_path") is not None:
            kwargs["stdout_path"].write_text(result.stdout)
            result.stdout = ""
        return result


class FakeExecutableLocator:
    def __init__(self, available: Optional[Sequence[str]] = None) -> None:
        defaults = ("php", "wp", "ssh", "rsync", "mysql", "mysqldump", "git")
        self.available = set(defaults if available is None else available)
        self.calls: List[str] = []

    def which(self, executable: str) -> Optional[str]:
        self.calls.append(executable)
        return f"/usr/bin/{executable}" if executable in self.available else None


class FakeFileSystem:
    def __init__(self, files: Optional[Dict[Path, str]] = None, fingerprint: str = "same") -> None:
        self.files = files or {}
        self.current_fingerprint = fingerprint
        self.removed: List[Path] = []
        self.backups: Dict[Path, str] = {}

    def exists(self, path: Path) -> bool:
        return path in self.files

    def read_text(self, path: Path) -> str:
        return self.files[path]

    def fingerprint(self, path: Path) -> str:
        return self.current_fingerprint

    def remove_tree(self, path: Path) -> None:
        self.removed.append(path)

    def is_symlink(self, path: Path) -> bool:
        return False

    def create_temporary_directory(self, parent: Path, prefix: str) -> Path:
        path = parent / f"{prefix}temporary"
        self.files[path] = ""
        return path

    def move(self, source: Path, destination: Path) -> None:
        if source in self.files:
            self.files[destination] = self.files.pop(source)

    def create_immutable_backup(self, source: Path, destination: Path) -> str:
        fingerprint = f"backup:{source}"
        self.backups[destination] = fingerprint
        return fingerprint

    def verify_backup(self, path: Path, fingerprint: str) -> bool:
        return self.backups.get(path) == fingerprint


def health(status: HealthStatus) -> CapabilityReport:
    return CapabilityReport(
        (ProbeResult(Capability.WPCLI_FULL_BOOTSTRAP, status is HealthStatus.HEALTHY),), status
    )


class FakeProbe:
    def __init__(self, reports: List[CapabilityReport]) -> None:
        self.reports = reports
        self.calls: List[Path] = []
        self.requirements: List[set[Capability]] = []

    def probe(
        self,
        installation_path: Path,
        required_capabilities: set[Capability] | None = None,
    ) -> CapabilityReport:
        self.calls.append(installation_path)
        required = required_capabilities or set()
        self.requirements.append(required)
        report = self.reports.pop(0) if len(self.reports) > 1 else self.reports[0]
        present = {result.capability for result in report.results}
        additions = tuple(
            ProbeResult(capability, True, "fake disponível")
            for capability in required
            if capability not in present
        )
        return CapabilityReport((*report.results, *additions), report.health, report.fatal_errors)


class FakeStateStore:
    def __init__(self) -> None:
        self.manifests: Dict[tuple, RunManifest] = {}
        self.checkpoints: List[str] = []
        self.preflight_calls = 0

    def preflight(self) -> None:
        self.preflight_calls += 1

    def create_run(self, manifest: RunManifest) -> None:
        self.manifests[(manifest.installation_id, manifest.run_id)] = manifest

    def save_manifest(self, manifest: RunManifest) -> None:
        self.manifests[(manifest.installation_id, manifest.run_id)] = manifest

    def load_manifest(self, installation_id: str, run_id: str) -> RunManifest:
        return self.manifests[(installation_id, run_id)]

    def save_checkpoint(
        self, installation_id: str, run_id: str, step: StepResult, report: CapabilityReport
    ) -> None:
        self.checkpoints.append(step.name)


class FakeClock:
    def now_iso(self) -> str:
        return "2026-01-01T00:00:00+00:00"


class FakeIds:
    def new(self) -> str:
        return "run-1"


class FakeOperations:
    def __init__(self, fail_at: Optional[str] = None) -> None:
        self.fail_at = fail_at
        self.calls: List[str] = []
        self.validation_calls: List[str] = []
        self.contexts: List[Dict[str, Any]] = []

    def execute(self, step_name: str, context: Dict[str, Any]) -> StepResult:
        self.calls.append(step_name)
        self.contexts.append(context)
        status = StepStatus.FAILED if step_name == self.fail_at else StepStatus.SUCCEEDED
        planned = context.get("planned_step")
        changed = (
            status is StepStatus.SUCCEEDED
            and getattr(planned, "capability", None) is not StepCapability.READ_ONLY
        )
        return StepResult(step_name, status, changed, step_name)

    def validate(self, step_name: str, context: Dict[str, Any]) -> StepResult:
        self.validation_calls.append(step_name)
        self.contexts.append(context)
        status = StepStatus.FAILED if step_name == self.fail_at else StepStatus.VALIDATED
        return StepResult(step_name, status, False, step_name)
