from __future__ import annotations

from typing import Protocol

from wp_modernizer.domain.models import CapabilityReport, RunManifest, StepResult


class ProgressReporter(Protocol):
    """Receives pipeline lifecycle events without depending on a presentation framework."""

    def run_started(self, manifest: RunManifest, total_steps: int) -> None: ...

    def capabilities_checked(self, stage: str, report: CapabilityReport) -> None: ...

    def step_started(self, name: str, index: int, total: int) -> None: ...

    def step_finished(self, result: StepResult, index: int, total: int) -> None: ...

    def run_finished(self, manifest: RunManifest) -> None: ...

    def run_failed(self, manifest: RunManifest, reason: str) -> None: ...


class NullProgressReporter:
    """Default reporter for programmatic callers that do not need progress events."""

    def run_started(self, manifest: RunManifest, total_steps: int) -> None:
        pass

    def capabilities_checked(self, stage: str, report: CapabilityReport) -> None:
        pass

    def step_started(self, name: str, index: int, total: int) -> None:
        pass

    def step_finished(self, result: StepResult, index: int, total: int) -> None:
        pass

    def run_finished(self, manifest: RunManifest) -> None:
        pass

    def run_failed(self, manifest: RunManifest, reason: str) -> None:
        pass
