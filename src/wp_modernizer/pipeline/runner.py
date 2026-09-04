from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Any, Dict, Iterable, Set

from wp_modernizer.application.ports import CapabilityProbePort, Clock, FileSystem, StateStore
from wp_modernizer.domain.enums import (
    Capability,
    HealthStatus,
    RunStatus,
    StepCapability,
    StepStatus,
)
from wp_modernizer.domain.errors import MissingCapabilityError, ResumeConsistencyError
from wp_modernizer.domain.models import CapabilityReport, RunManifest, StepResult

from .progress import NullProgressReporter, ProgressReporter
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
        required_capabilities: Set[Capability] | None = None,
        reporter: ProgressReporter | None = None,
    ) -> RunManifest:
        progress = reporter or NullProgressReporter()
        ordered_steps = tuple(steps)
        total_steps = len(ordered_steps)
        progress.run_started(manifest, total_steps)
        requirements = required_capabilities
        before = (
            self._probe.probe(installation_path, requirements)
            if requirements is not None
            else self._probe.probe(installation_path)
        )
        progress.capabilities_checked("before", before)
        missing_external = tuple(
            sorted(
                (capability for capability in requirements or set() if not before.has(capability)),
                key=lambda capability: capability.value,
            )
        )
        if missing_external:
            missing = ", ".join(capability.value for capability in missing_external)
            progress.run_failed(manifest, f"capabilities obrigatórias ausentes: {missing}")
            raise MissingCapabilityError(f"capabilities obrigatórias ausentes: {missing}")
        manifest.health_before = before.health
        self._record_diagnostics(manifest, before)
        manifest.status = RunStatus.RUNNING
        self._state.create_run(manifest)
        for index, step in enumerate(ordered_steps, start=1):
            progress.step_started(step.name, index, total_steps)
            try:
                result, requires_post_step_probe = self._execute_step(
                    manifest, step, context, before
                )
            except Exception as exc:
                progress.run_failed(manifest, str(exc))
                raise
            manifest.steps.append(result)
            if not requires_post_step_probe:
                progress.step_finished(result, index, total_steps)
                continue
            # This post-step probe is also the final validation when ``step`` is the
            # last executable step.  Keep it here instead of representing that same
            # probe as a separate, no-op health-check step in plans and manifests.
            after = (
                self._probe.probe(installation_path, requirements)
                if requirements is not None
                else self._probe.probe(installation_path)
            )
            manifest.health_after = after.health
            self._record_diagnostics(manifest, after)
            progress.capabilities_checked("after_step", after)
            self._state.save_checkpoint(manifest.installation_id, manifest.run_id, result, after)
            # Keep recovery data produced by the step durable before the next mutable step.
            # In particular, the widget reference snapshot must survive an interruption in
            # any subsequent WordPress update, not only a normally handled pipeline failure.
            self._state.save_manifest(manifest)
            expected_status = StepStatus.VALIDATED if manifest.dry_run else StepStatus.EXECUTED
            regressed = self._regressed(before.health, after.health)
            allowed_transient_health = regressed and after.health in step.allowed_health_regressions
            if result.status is not expected_status or (regressed and not allowed_transient_health):
                manifest.failed_step = step.name
                manifest.status = RunStatus.UPDATE_FAILED_PRESERVED
                manifest.finished_at = self._clock.now_iso()
                manifest.filesystem_fingerprint = self._filesystem.fingerprint(installation_path)
                self._state.save_manifest(manifest)
                progress.step_finished(result, index, total_steps)
                progress.run_failed(manifest, result.message)
                return manifest
            manifest.last_successful_step = step.name
            progress.step_finished(result, index, total_steps)
            before = after
        if not manifest.dry_run:
            manifest.status = RunStatus.EXECUTED
        elif all(step.status is StepStatus.VALIDATED for step in manifest.steps):
            manifest.status = RunStatus.VALIDATED
        else:
            manifest.status = RunStatus.PLANNED
        manifest.finished_at = self._clock.now_iso()
        manifest.filesystem_fingerprint = self._filesystem.fingerprint(installation_path)
        self._state.save_manifest(manifest)
        progress.run_finished(manifest)
        return manifest

    @staticmethod
    def _execute_step(
        manifest: RunManifest,
        step: Step,
        context: Dict[str, Any],
        before: CapabilityReport,
    ) -> tuple[StepResult, bool]:
        missing_requirements = tuple(
            capability for capability in step.dry_run_requirements if not before.has(capability)
        )
        if manifest.dry_run and missing_requirements:
            missing = ", ".join(item.value for item in missing_requirements)
            return (
                StepResult(
                    step.name,
                    StepStatus.PLANNED,
                    False,
                    f"dry-run: validação indisponível; capacidades ausentes: {missing}",
                    installation_id=step.installation_id or manifest.installation_id,
                ),
                False,
            )
        if manifest.dry_run and step.capability is StepCapability.MUTABLE_WITHOUT_SAFE_DRY_RUN:
            return (
                StepResult(
                    step.name,
                    StepStatus.PLANNED,
                    False,
                    "dry-run: sem alteração",
                    installation_id=step.installation_id or manifest.installation_id,
                ),
                False,
            )
        if manifest.dry_run and step.capability is StepCapability.MUTABLE_WITH_NATIVE_DRY_RUN:
            result = step.validate(context)
            if result.changed or result.status is StepStatus.EXECUTED:
                raise RuntimeError(
                    f"Validação nativa {step.name} declarou execução ou mutação em dry-run"
                )
        else:
            result = step.execute(context)
            if manifest.dry_run and result.status is StepStatus.EXECUTED:
                if result.changed:
                    raise RuntimeError(
                        f"Etapa somente leitura {step.name} declarou mutação em dry-run"
                    )
                result = replace(result, status=StepStatus.VALIDATED)
        if result.installation_id is None:
            result = replace(
                result, installation_id=step.installation_id or manifest.installation_id
            )
        return result, True

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
