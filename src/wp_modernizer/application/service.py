from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
from typing import Any, Dict, cast

from wp_modernizer.application.ports import (
    CapabilityProbePort,
    Clock,
    FileSystem,
    IdGenerator,
    MutableOperations,
    StateStore,
)
from wp_modernizer.config.models import ApplicationConfig
from wp_modernizer.domain.enums import (
    Environment,
    Operation,
    PendingOperationType,
    RunStatus,
    StepStatus,
)
from wp_modernizer.domain.errors import (
    ConfigurationError,
    ResumeConsistencyError,
    UnsafeOperationError,
)
from wp_modernizer.domain.models import MigrationPlan, PendingOperation, PlannedStep, RunManifest
from wp_modernizer.domain.path_parser import InstallationPathParser
from wp_modernizer.domain.planning import MigrationPlanner
from wp_modernizer.pipeline.runner import PipelineRunner
from wp_modernizer.pipeline.steps import OperationStep, planned_update_steps


class ModernizerService:
    def __init__(
        self,
        config: ApplicationConfig,
        probe: CapabilityProbePort,
        state: StateStore,
        filesystem: FileSystem,
        clock: Clock,
        ids: IdGenerator,
        operations: MutableOperations,
    ) -> None:
        self.config = config
        self._probe = probe
        self._state = state
        self._filesystem = filesystem
        self._clock = clock
        self._ids = ids
        self._operations = operations
        self._parser = InstallationPathParser(config.allowed_app_roots)
        self._runner = PipelineRunner(probe, state, filesystem, clock)

    def diagnose(self, installation_id: str) -> Dict[str, Any]:
        item = self._installation(installation_id)
        report = self._probe.probe(item.destination_path)
        return {
            "installation": installation_id,
            "path": str(item.destination_path),
            "capabilities": [asdict(result) for result in report.results],
            "health": report.health.value,
            "fatal_errors": report.fatal_errors,
        }

    def inventory(self, installation_id: str) -> Dict[str, Any]:
        item = self._installation(installation_id)
        report = self.diagnose(installation_id)
        parsed = self._parser.parse(str(item.destination_path), installation_id, Environment.TEST)
        unavailable = "indisponível: capacidade ausente"
        report.update(
            {
                "domain": parsed.domain,
                "instance_name": parsed.instance_name,
                "nested_path": str(parsed.relative_nested_path)
                if parsed.relative_nested_path
                else None,
                "multisite": any(
                    cap["capability"].value == "MULTISITE" and cap["available"]
                    for cap in report["capabilities"]
                ),
                "wordpress_version": unavailable,
                "php_version": unavailable,
                "wpcli_version": unavailable,
                "active_theme": unavailable,
                "plugins": unavailable,
                "managed_plugins": [plugin.slug for plugin in self.config.managed_plugins],
                "database_name": item.database_override or unavailable,
                "database_endpoint_id": item.allowed_database_endpoints,
                "site_url": unavailable,
                "widget_counts": unavailable,
            }
        )
        return report

    def plan(self, installation_id: str) -> Dict[str, Any]:
        return cast(
            Dict[str, Any], self._serializable(asdict(self._migration_plan(installation_id)))
        )

    def _migration_plan(self, installation_id: str) -> MigrationPlan:
        item = self._installation(installation_id)
        installations = []
        for key, candidate in self.config.installations.items():
            try:
                parsed = self._parser.parse(str(candidate.destination_path), key, Environment.TEST)
            except UnsafeOperationError:
                continue
            if (
                candidate.destination_path == item.destination_path
                or item.destination_path in candidate.destination_path.parents
            ):
                installations.append(parsed)
        pending = (
            PendingOperation(
                PendingOperationType.SEARCH_REPLACE,
                {
                    "test_url": str(item.test_url) if item.test_url is not None else "",
                    "organizational_domain": self.config.organizational_domain,
                },
                "executa somente após uma simulação bem-sucedida com WP-CLI reduzido",
            ),
        )
        return MigrationPlanner().build(
            installation_id,
            item.source_environment,
            item.source_server,
            item.database_override
            or self.config.database_overrides.get(installation_id)
            or "resolved-at-runtime",
            installations,
            pending,
        )

    def execute(
        self,
        operation: Operation,
        installation_id: str,
        *,
        dry_run: bool,
        replace_existing: bool = False,
        restore_widgets: bool = False,
    ) -> RunManifest:
        item = self._installation(installation_id)
        path = item.destination_path
        migration_plan = self._migration_plan(installation_id)
        update_steps = planned_update_steps(installation_id)
        if operation is Operation.MIGRATE:
            planned_steps = migration_plan.steps
        elif operation is Operation.UPDATE:
            planned_steps = update_steps
        elif operation is Operation.PIPELINE:
            planned_steps = migration_plan.steps + tuple(
                step for step in update_steps if step.name != "pending_search_replace"
            )
        else:
            raise ConfigurationError(f"Operação de execução não suportada: {operation.value}")
        run_id = self._ids.new()
        manifest = RunManifest(
            run_id,
            installation_id,
            operation,
            RunStatus.RUNNING,
            self._clock.now_iso(),
            dry_run,
            pending_operations=list(migration_plan.pending_operations),
            planned_steps=list(planned_steps),
            migration_plan=migration_plan,
            execution_parameters={
                "replace_existing": replace_existing,
                "restore_widgets": restore_widgets,
            },
            recovery_data={},
            original_run_id=run_id,
        )
        context = {
            "run_id": run_id,
            "installation_id": installation_id,
            "installation": item,
            "replace_existing": replace_existing,
            "restore_widgets": restore_widgets,
            "installations": self.config.installations,
            "migration_plan": migration_plan,
            "recovery_data": manifest.recovery_data,
            "manifest": manifest,
        }
        steps = tuple(OperationStep(step, self._operations) for step in planned_steps)
        return self._runner.run(manifest, path, steps, context)

    def resume(self, installation_id: str, run_id: str, dry_run: bool) -> RunManifest:
        old = self._state.load_manifest(installation_id, run_id)
        original_steps = self._safe_resume_plan(old)
        path = self._installation(installation_id).destination_path
        self._runner.assert_resume_consistent(old, path)
        completed_count = self._completed_prefix(old, original_steps)
        remaining = original_steps[completed_count:]
        parameters = dict(old.execution_parameters or {})
        new = RunManifest(
            self._ids.new(),
            installation_id,
            old.operation,
            RunStatus.RUNNING,
            self._clock.now_iso(),
            dry_run,
            steps=list(old.steps[:completed_count]),
            pending_operations=list(old.pending_operations),
            last_successful_step=(old.steps[completed_count - 1].name if completed_count else None),
            widget_diff=list(old.widget_diff),
            planned_steps=list(original_steps),
            migration_plan=old.migration_plan,
            execution_parameters=parameters,
            recovery_data={key: dict(value) for key, value in old.recovery_data.items()},
            original_run_id=old.original_run_id or old.run_id,
            resumed_from_run_id=old.run_id,
            resume_source_failed_step=old.failed_step,
        )
        return self._runner.run(
            new,
            path,
            [OperationStep(step, self._operations) for step in remaining],
            {
                "run_id": new.run_id,
                "installation_id": installation_id,
                "installation": self._installation(installation_id),
                "installations": self.config.installations,
                "migration_plan": old.migration_plan,
                "replace_existing": parameters["replace_existing"],
                "restore_widgets": parameters["restore_widgets"],
                "recovery_data": new.recovery_data,
                "resumed_from": run_id,
                "manifest": new,
            },
        )

    @staticmethod
    def _safe_resume_plan(old: RunManifest) -> list[PlannedStep]:
        executable = {Operation.MIGRATE, Operation.UPDATE, Operation.PIPELINE}
        missing = []
        if old.operation not in executable:
            missing.append("operação original")
        if not old.planned_steps:
            missing.append("plano original ordenado")
        if old.execution_parameters is None or not {
            "replace_existing",
            "restore_widgets",
        }.issubset(old.execution_parameters):
            missing.append("parâmetros de execução")
        if old.migration_plan is None:
            missing.append("plano de migração e operações pendentes")
        if missing:
            detail = ", ".join(missing)
            raise ResumeConsistencyError(
                "Este manifest não contém informação suficiente para um resume seguro: "
                f"{detail}. Execute novamente a operação original."
            )
        return list(old.planned_steps)

    @staticmethod
    def _completed_prefix(old: RunManifest, planned_steps: list[PlannedStep]) -> int:
        if len(old.steps) > len(planned_steps):
            raise ResumeConsistencyError(
                "O histórico de steps não corresponde ao plano original; resume seguro recusado"
            )
        completed = 0
        encountered_incomplete = False
        for index, result in enumerate(old.steps):
            planned = planned_steps[index]
            identity = (result.installation_id or old.installation_id, result.name)
            expected = (planned.installation_id or old.installation_id, planned.name)
            if identity != expected:
                raise ResumeConsistencyError(
                    "O histórico de steps não corresponde ao plano original; resume seguro recusado"
                )
            if result.status is StepStatus.SUCCEEDED:
                if encountered_incomplete:
                    raise ResumeConsistencyError(
                        "O histórico possui steps concluídos fora de ordem; resume seguro recusado"
                    )
                completed += 1
            else:
                encountered_incomplete = True
        return completed

    def _installation(self, installation_id: str) -> Any:
        try:
            return self.config.installations[installation_id]
        except KeyError as exc:
            raise ConfigurationError(f"Instalação desconhecida: {installation_id}") from exc

    @classmethod
    def _serializable(cls, value: Any) -> Any:
        if hasattr(value, "value"):
            return value.value
        if isinstance(value, Path):
            return str(value)
        if isinstance(value, dict):
            return {key: cls._serializable(item) for key, item in value.items()}
        if isinstance(value, (list, tuple)):
            return [cls._serializable(item) for item in value]
        return value
