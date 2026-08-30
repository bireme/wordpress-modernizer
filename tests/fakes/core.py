from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from wp_modernizer.domain.enums import Capability, HealthStatus, StepStatus
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
        return self.results.pop(0) if self.results else FakeCommandResult()


class FakeFileSystem:
    def __init__(self, files: Optional[Dict[Path, str]] = None, fingerprint: str = "same") -> None:
        self.files = files or {}
        self.current_fingerprint = fingerprint
        self.removed: List[Path] = []

    def exists(self, path: Path) -> bool:
        return path in self.files

    def read_text(self, path: Path) -> str:
        return self.files[path]

    def fingerprint(self, path: Path) -> str:
        return self.current_fingerprint

    def remove_tree(self, path: Path) -> None:
        self.removed.append(path)


def health(status: HealthStatus) -> CapabilityReport:
    return CapabilityReport(
        (ProbeResult(Capability.WPCLI_FULL_BOOTSTRAP, status is HealthStatus.HEALTHY),), status
    )


class FakeProbe:
    def __init__(self, reports: List[CapabilityReport]) -> None:
        self.reports = reports

    def probe(self, installation_path: Path) -> CapabilityReport:
        return self.reports.pop(0) if len(self.reports) > 1 else self.reports[0]


class FakeStateStore:
    def __init__(self) -> None:
        self.manifests: Dict[tuple, RunManifest] = {}
        self.checkpoints: List[str] = []

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

    def execute(self, step_name: str, context: Dict[str, Any]) -> StepResult:
        self.calls.append(step_name)
        status = StepStatus.FAILED if step_name == self.fail_at else StepStatus.SUCCEEDED
        return StepResult(step_name, status, status is StepStatus.SUCCEEDED, step_name)
