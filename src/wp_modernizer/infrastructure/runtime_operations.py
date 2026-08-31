from __future__ import annotations

import tempfile
from dataclasses import replace
from pathlib import Path
from typing import Any, ClassVar, Dict, Tuple

from wp_modernizer.application.ports import DatabasePort, FileTransferPort, WordPressPort
from wp_modernizer.domain.database import DatabaseLocator, SuffixDatabaseNamingStrategy
from wp_modernizer.domain.enums import Environment, PendingOperationType, StepStatus
from wp_modernizer.domain.errors import (
    ConfigurationError,
    InfrastructureError,
    UnsafeOperationError,
    WordPressUnavailableError,
)
from wp_modernizer.domain.models import PlannedStep, StepResult
from wp_modernizer.domain.path_parser import InstallationPathParser
from wp_modernizer.domain.test_url import OrganizationalTestUrlPolicy


class RuntimeOperations:
    """Traduz etapas planejadas em chamadas às portas concretas de infraestrutura."""

    _wp_commands: ClassVar[Dict[str, Tuple[str, ...]]] = {
        "core_update": ("core", "update"),
        "core_database_update": ("core", "update-db"),
        "third_party_plugin_update": ("plugin", "update", "--all"),
        "theme_update": ("theme", "update", "--all"),
        "core_languages": ("language", "core", "update"),
        "plugin_languages": ("language", "plugin", "update", "--all"),
        "theme_languages": ("language", "theme", "update", "--all"),
    }

    def __init__(
        self,
        files: FileTransferPort,
        databases: DatabasePort,
        wordpress: WordPressPort,
        parser: InstallationPathParser,
        *,
        database_overrides: Dict[str, str] | None = None,
    ) -> None:
        self._files = files
        self._databases = databases
        self._wordpress = wordpress
        self._parser = parser
        self._database_overrides = database_overrides or {}
        self._database_runs: Dict[tuple[str, str], Dict[str, Any]] = {}

    def execute(self, step_name: str, context: Dict[str, Any]) -> StepResult:
        planned_step = context.get("planned_step")
        if not isinstance(planned_step, PlannedStep):
            raise UnsafeOperationError(f"Etapa {step_name} não possui plano de execução")
        installation = context.get("installations", {}).get(
            planned_step.installation_id, context["installation"]
        )
        if installation.destination_environment is not Environment.TEST:
            raise UnsafeOperationError("Operações mutáveis são proibidas fora de TESTE")
        path = Path(installation.destination_path)
        run_id = str(context["run_id"])

        if step_name == "backup_existing_test":
            if not path.exists():
                return self._ok(step_name, False, "não há cópia de teste existente")
            if not context.get("replace_existing"):
                return self._failed(
                    step_name, "a cópia de teste existente requer --replace-existing"
                )
            parsed = self._parser.parse(
                str(path), planned_step.installation_id, installation.destination_environment
            )
            self._parser.assert_safe_destructive_target(path, parsed)
            return self._failed(
                step_name,
                "o adaptador de cópia de segurança deve ser configurado antes da substituição; "
                "o teste existente foi preservado",
            )

        if step_name == "copy_files":
            parsed = self._parser.parse(
                str(path), planned_step.installation_id, installation.destination_environment
            )
            self._parser.assert_safe_destructive_target(path, parsed)
            server = self._files.get_server(installation.source_server)
            if server.environment is not installation.source_environment:
                raise UnsafeOperationError("O ambiente do servidor não coincide com o da origem")
            try:
                elapsed = self._files.copy_from(
                    installation.source_server,
                    Path(installation.source_path),
                    path.parent,
                    planned_step.excludes,
                    run_id,
                )
            except InfrastructureError as exc:
                return self._failed(step_name, str(exc))
            return StepResult(
                step_name,
                StepStatus.SUCCEEDED,
                True,
                "arquivos copiados pelo transporte SSH configurado",
                {"duration_seconds": float(elapsed)},
            )

        if step_name == "snapshot_source_database":
            return self._snapshot_source_database(
                step_name,
                planned_step.installation_id,
                installation,
                path,
                run_id,
                context.get("recovery_data", {}),
            )
        if step_name == "copy_database":
            return self._copy_database(
                step_name,
                planned_step.installation_id,
                run_id,
                context.get("recovery_data", {}),
            )
        if step_name == "write_test_db_config":
            return self._write_test_db_config(
                step_name,
                planned_step.installation_id,
                path,
                run_id,
                context.get("recovery_data", {}),
            )

        if step_name in {"preflight", "snapshot", "widget_validation", "final_health_check"}:
            return self._ok(step_name, False, "ponto de controle de diagnóstico concluído")
        if step_name == "pending_search_replace":
            return self._search_replace(step_name, path, context, run_id)
        if step_name == "managed_plugin_refresh":
            return self._ok(step_name, False, "nenhuma atualização de plugin gerenciado solicitada")
        command = self._wp_commands.get(step_name)
        if command:
            output = self._wordpress.update(path, command, run_id)
            return StepResult(step_name, StepStatus.SUCCEEDED, True, output)
        raise UnsafeOperationError(f"Etapa mutável desconhecida: {step_name}")

    def _snapshot_source_database(
        self,
        step_name: str,
        installation_id: str,
        installation: Any,
        path: Path,
        run_id: str,
        recovery_data: Dict[str, Dict[str, str]],
    ) -> StepResult:
        source_name = installation.database_override or self._wordpress.get_config(
            path, "DB_NAME", run_id
        )
        source_endpoints = [
            endpoint_id
            for endpoint_id in installation.allowed_database_endpoints
            if self._databases.get_database(endpoint_id).environment
            is installation.source_environment
            and source_name in self._databases.list_schemas(endpoint_id)
        ]
        if len(source_endpoints) != 1:
            return self._failed(
                step_name,
                "a origem MySQL não foi identificada de forma única entre os endpoints permitidos",
            )
        target_endpoints = [
            endpoint_id
            for endpoint_id in installation.allowed_database_endpoints
            if self._databases.get_database(endpoint_id).environment is Environment.TEST
        ]
        target = DatabaseLocator(self._databases, SuffixDatabaseNamingStrategy("test")).locate(
            source_name,
            installation.database_aliases,
            target_endpoints,
            self._database_overrides,
            installation_id,
        )
        self._database_runs[(run_id, installation_id)] = {
            "source_endpoint": source_endpoints[0],
            "source_database": source_name,
            "target_endpoint": target.endpoint_id,
            "target_database": target.database_name,
        }
        recovery_data[installation_id] = dict(self._database_runs[(run_id, installation_id)])
        return self._ok(step_name, False, "origem e destino MySQL resolvidos sem ambiguidade")

    def _copy_database(
        self,
        step_name: str,
        installation_id: str,
        run_id: str,
        recovery_data: Dict[str, Dict[str, str]],
    ) -> StepResult:
        key = (run_id, installation_id)
        state = self._database_runs.get(key) or recovery_data.get(installation_id)
        if not state:
            return self._failed(step_name, "não há instantâneo MySQL desta execução para importar")
        with tempfile.NamedTemporaryFile(
            prefix="wp-modernizer-", suffix=".sql", delete=False
        ) as handle:
            dump_path = Path(handle.name)
        try:
            self._databases.dump(
                state["source_endpoint"], state["source_database"], dump_path, run_id
            )
            self._databases.import_dump(
                state["target_endpoint"], state["target_database"], dump_path, run_id
            )
        finally:
            dump_path.unlink(missing_ok=True)
        return self._ok(step_name, True, "banco importado pelo adapter MySQL no ambiente de teste")

    def _write_test_db_config(
        self,
        step_name: str,
        installation_id: str,
        path: Path,
        run_id: str,
        recovery_data: Dict[str, Dict[str, str]],
    ) -> StepResult:
        key = (run_id, installation_id)
        state = self._database_runs.get(key) or recovery_data.get(installation_id)
        if not state:
            return self._failed(step_name, "o destino MySQL desta execução não foi resolvido")
        values = self._databases.wordpress_configuration(
            state["target_endpoint"], state["target_database"]
        )
        self._wordpress.set_config(path, values, run_id)
        self._database_runs.pop(key, None)
        recovery_data.pop(installation_id, None)
        return self._ok(step_name, True, "wp-config aponta para o banco do ambiente de teste")

    def _search_replace(
        self, step_name: str, path: Path, context: Dict[str, Any], run_id: str
    ) -> StepResult:
        plan = context.get("migration_plan")
        pending = next(
            (
                item
                for item in getattr(plan, "pending_operations", ())
                if item.operation_type is PendingOperationType.SEARCH_REPLACE and not item.completed
            ),
            None,
        )
        if pending is None:
            return self._ok(step_name, False, "nenhum search-replace pendente")
        explicit_url = pending.parameters.get("test_url") or None
        try:
            old_url = self._wordpress.get_site_url(path, run_id)
            if explicit_url is not None and old_url.rstrip("/") == explicit_url.rstrip("/"):
                return self._failed(
                    step_name, "search-replace recusado: URLs de origem e destino coincidem"
                )
            new_url = OrganizationalTestUrlPolicy(
                pending.parameters.get("organizational_domain", "")
            ).resolve(old_url, explicit_url)
            if old_url.rstrip("/") == new_url.rstrip("/"):
                return self._failed(
                    step_name, "search-replace recusado: URLs de origem e destino coincidem"
                )
            multisite = self._wordpress.is_multisite(path, run_id)
            changed_count = self._wordpress.search_replace(
                path, old_url, new_url, dry_run=False, multisite=multisite, run_id=run_id
            )
        except ConfigurationError as exc:
            return self._failed(step_name, str(exc))
        except UnsafeOperationError:
            return self._failed(
                step_name, "search-replace recusado por apontar para o ambiente de produção"
            )
        except WordPressUnavailableError:
            return self._failed(step_name, "search-replace falhou; a cópia de TESTE foi preservada")

        manifest = context.get("manifest")
        if manifest is not None:
            for index, operation in enumerate(manifest.pending_operations):
                if (
                    operation.operation_type is pending.operation_type
                    and operation.parameters == pending.parameters
                    and not operation.completed
                ):
                    manifest.pending_operations[index] = replace(operation, completed=True)
                    break
        return StepResult(
            step_name,
            StepStatus.SUCCEEDED,
            changed_count > 0,
            f"search-replace concluído: {changed_count} substituições",
            {"replacements": float(changed_count)},
        )

    @staticmethod
    def _ok(name: str, changed: bool, message: str) -> StepResult:
        return StepResult(name, StepStatus.SUCCEEDED, changed, message)

    @staticmethod
    def _failed(name: str, message: str) -> StepResult:
        return StepResult(name, StepStatus.FAILED, False, message)
