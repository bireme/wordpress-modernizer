from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Iterable

from wp_modernizer.application.ports import CapabilityProbePort, Clock, FileSystem, StateStore
from wp_modernizer.domain.enums import Capability, HealthStatus, RunStatus, StepStatus
from wp_modernizer.domain.errors import ResumeConsistencyError
from wp_modernizer.domain.models import CapabilityReport, RunManifest, StepResult

from .steps import Step


class PipelineRunner:
    """Para na primeira falha ou regressão e preserva o estado resultante."""

    def __init__(
        self,
        probe: CapabilityProbePort,
        state: StateStore,
        filesystem: FileSystem,
        clock: Clock,
    ) -> None:
        self._probe = probe
        self._state = state
        self._filesystem = filesystem
        self._clock = clock

    def run(
        self,
        manifest: RunManifest,
        installation_path: Path,
        steps: Iterable[Step],
        context: Dict[str, Any],
    ) -> RunManifest:
        before = self._probe.probe(installation_path)
        manifest.health_before = before.health
        self._record_diagnostics(manifest, before)
        manifest.status = RunStatus.RUNNING
        self._state.create_run(manifest)
        for step in steps:
            if manifest.dry_run and step.mutable:
                result = StepResult(step.name, StepStatus.PLANNED, False, "dry-run: sem alteração")
                manifest.steps.append(result)
                continue
            result = step.execute(context)
            manifest.steps.append(result)
            after = self._probe.probe(installation_path)
            manifest.health_after = after.health
            self._record_diagnostics(manifest, after)
            self._state.save_checkpoint(manifest.installation_id, manifest.run_id, result, after)
            if result.status is not StepStatus.SUCCEEDED or self._regressed(
                before.health, after.health
            ):
                manifest.failed_step = step.name
                manifest.status = RunStatus.UPDATE_FAILED_PRESERVED
                manifest.finished_at = self._clock.now_iso()
                manifest.filesystem_fingerprint = self._filesystem.fingerprint(installation_path)
                self._state.save_manifest(manifest)
                return manifest
            manifest.last_successful_step = step.name
            before = after
        manifest.status = RunStatus.SUCCEEDED if not manifest.dry_run else RunStatus.PLANNED
        manifest.finished_at = self._clock.now_iso()
        manifest.filesystem_fingerprint = self._filesystem.fingerprint(installation_path)
        self._state.save_manifest(manifest)
        return manifest

    def assert_resume_consistent(self, manifest: RunManifest, installation_path: Path) -> None:
        current = self._filesystem.fingerprint(installation_path)
        if manifest.filesystem_fingerprint and current != manifest.filesystem_fingerprint:
            raise ResumeConsistencyError(
                "Intervenção manual detectada; inspecione as diferenças antes de retomar"
            )

    @staticmethod
    def _regressed(before: HealthStatus, after: HealthStatus) -> bool:
        rank = {
            HealthStatus.HEALTHY: 6,
            HealthStatus.PLUGIN_OR_THEME_CONFLICT: 5,
            HealthStatus.WPCLI_PARTIAL: 4,
            HealthStatus.PRE_BOOTSTRAP_RECOVERY_REQUIRED: 3,
            HealthStatus.CORE_INCOMPLETE: 2,
            HealthStatus.DATABASE_UNAVAILABLE: 1,
            HealthStatus.PHP_CONFIG_ERROR: 1,
            HealthStatus.UNKNOWN: 0,
        }
        return rank[after] < rank[before]

    @staticmethod
    def _record_diagnostics(manifest: RunManifest, report: CapabilityReport) -> None:
        manifest.wpcli_full_bootstrap = report.has(Capability.WPCLI_FULL_BOOTSTRAP)
        manifest.wpcli_reduced_bootstrap = report.has(Capability.WPCLI_REDUCED_BOOTSTRAP)
        manifest.fatal_errors = list(report.fatal_errors)
