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
from wp_modernizer.domain.enums import Environment, Operation, PendingOperationType, RunStatus
from wp_modernizer.domain.errors import ConfigurationError, UnsafeOperationError
from wp_modernizer.domain.models import PendingOperation, RunManifest
from wp_modernizer.domain.path_parser import InstallationPathParser
from wp_modernizer.domain.planning import MigrationPlanner
from wp_modernizer.pipeline.runner import PipelineRunner
from wp_modernizer.pipeline.steps import UPDATE_STEP_NAMES, OperationStep


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
                {"old_url": "discovered-at-runtime", "new_url": "configured-test-url"},
                "executa somente após uma simulação bem-sucedida com WP-CLI reduzido",
            ),
        )
        plan = MigrationPlanner().build(
            installation_id,
            item.source_environment,
            item.source_server,
            item.database_override or "resolved-at-runtime",
            installations,
            pending,
        )
        return cast(Dict[str, Any], self._serializable(asdict(plan)))

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
        migration_names = tuple(step["name"] for step in self.plan(installation_id)["steps"])
        if operation is Operation.MIGRATE:
            names = migration_names
        elif operation is Operation.UPDATE:
            names = UPDATE_STEP_NAMES
        elif operation is Operation.PIPELINE:
            names = migration_names + UPDATE_STEP_NAMES
        else:
            raise ConfigurationError(f"Operação de execução não suportada: {operation.value}")
        run_id = self._ids.new()
        manifest = RunManifest(
            run_id, installation_id, operation, RunStatus.RUNNING, self._clock.now_iso(), dry_run
        )
        context = {
            "run_id": run_id,
            "installation_id": installation_id,
            "installation": item,
            "replace_existing": replace_existing,
            "restore_widgets": restore_widgets,
        }
        steps = tuple(OperationStep(name, self._operations) for name in names)
        return self._runner.run(manifest, path, steps, context)

    def resume(self, installation_id: str, run_id: str, dry_run: bool) -> RunManifest:
        old = self._state.load_manifest(installation_id, run_id)
        path = self._installation(installation_id).destination_path
        self._runner.assert_resume_consistent(old, path)
        completed = {step.name for step in old.steps if step.status.value == "SUCCEEDED"}
        remaining = [name for name in UPDATE_STEP_NAMES if name not in completed]
        new = RunManifest(
            self._ids.new(),
            installation_id,
            Operation.RESUME,
            RunStatus.RUNNING,
            self._clock.now_iso(),
            dry_run,
        )
        return self._runner.run(
            new,
            path,
            [OperationStep(name, self._operations) for name in remaining],
            {
                "run_id": new.run_id,
                "installation_id": installation_id,
                "installation": self._installation(installation_id),
                "resumed_from": run_id,
            },
        )

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
