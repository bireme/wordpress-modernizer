from __future__ import annotations

import tempfile
from dataclasses import replace
from hashlib import sha256
from pathlib import Path
from typing import Any, ClassVar, Dict, Tuple

from wp_modernizer.application.ports import (
    DatabasePort,
    FileSystem,
    FileTransferPort,
    ManagedPluginPort,
    SourceWordPressPort,
    WordPressPort,
)
from wp_modernizer.domain.database import DatabaseLocator, ProductionTestDatabaseNamingStrategy
from wp_modernizer.domain.enums import (
    Environment,
    ManagedPluginStatus,
    PendingOperationType,
    StepCapability,
    StepStatus,
)
from wp_modernizer.domain.errors import (
    AmbiguousDatabaseError,
    ConfigurationError,
    DatabaseNotFoundError,
    InfrastructureError,
    UnsafeOperationError,
    WordPressUnavailableError,
)
from wp_modernizer.domain.models import PlannedStep, StepResult
from wp_modernizer.domain.path_parser import InstallationPathParser
from wp_modernizer.domain.test_url import OrganizationalTestUrlPolicy
from wp_modernizer.domain.widgets import WidgetEvent, compare_widgets


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
        managed_plugins: ManagedPluginPort | None = None,
        source_wordpress: SourceWordPressPort | None = None,
        filesystem: FileSystem | None = None,
        organizational_domain: str = "bireme.org",
    ) -> None:
        self._files = files
        self._databases = databases
        self._wordpress = wordpress
        self._parser = parser
        self._database_overrides = database_overrides or {}
        self._managed_plugins = managed_plugins
        self._source_wordpress = source_wordpress
        self._filesystem = filesystem
        self._test_url_policy = OrganizationalTestUrlPolicy(organizational_domain)
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
            if self._filesystem is None:
                return self._failed(step_name, "o adaptador de backup local não está configurado")
            if not self._filesystem.exists(path):
                return self._ok(step_name, False, "não há cópia de teste existente")
            if not context.get("replace_existing"):
                return self._failed(
                    step_name, "a cópia de teste existente requer --replace-existing"
                )
            if self._filesystem.is_symlink(path):
                raise UnsafeOperationError("Destino destrutivo recusado por ser um link simbólico")
            parsed = self._parser.parse(
                str(path), planned_step.installation_id, installation.destination_environment
            )
            self._parser.assert_safe_destructive_target(
                path, parsed, is_symlink=self._filesystem.is_symlink(path)
            )
            backup_path = self._backup_path(parsed.app_root, run_id, planned_step.installation_id)
            recovery = context.get("recovery_data", {}).setdefault(planned_step.installation_id, {})
            recorded_path = recovery.get("backup_path")
            recorded_fingerprint = recovery.get("backup_fingerprint")
            if recorded_path == str(backup_path) and recorded_fingerprint:
                if self._filesystem.verify_backup(backup_path, recorded_fingerprint):
                    return self._ok(step_name, False, "backup imutável existente foi revalidado")
                return self._failed(step_name, "o backup registrado não passou na revalidação")
            try:
                fingerprint = self._filesystem.create_immutable_backup(path, backup_path)
            except (InfrastructureError, OSError) as exc:
                return self._failed(
                    step_name, f"backup falhou; a cópia de TESTE foi preservada: {exc}"
                )
            if not self._filesystem.verify_backup(backup_path, fingerprint):
                return self._failed(
                    step_name, "backup falhou na verificação; a cópia de TESTE foi preservada"
                )
            recovery.update(
                {
                    "backup_path": str(backup_path),
                    "backup_fingerprint": fingerprint,
                    "backup_source_path": str(path),
                    "backup_run_id": run_id,
                }
            )
            return self._ok(
                step_name, True, "backup imutável da cópia de TESTE criado e verificado"
            )

        if step_name == "copy_files":
            if self._filesystem is not None and self._filesystem.is_symlink(path):
                raise UnsafeOperationError("Destino destrutivo recusado por ser um link simbólico")
            parsed = self._parser.parse(
                str(path), planned_step.installation_id, installation.destination_environment
            )
            self._parser.assert_safe_destructive_target(
                path,
                parsed,
                is_symlink=self._filesystem.is_symlink(path) if self._filesystem else False,
            )
            server = self._files.get_server(installation.source_server)
            if server.environment is not installation.source_environment:
                raise UnsafeOperationError("O ambiente do servidor não coincide com o da origem")
            try:
                if self._filesystem is not None and self._filesystem.exists(path):
                    recovery = context.get("recovery_data", {}).get(
                        planned_step.installation_id, {}
                    )
                    backup = recovery.get("backup_path")
                    fingerprint = recovery.get("backup_fingerprint")
                    if (
                        not backup
                        or not fingerprint
                        or not self._filesystem.verify_backup(Path(backup), fingerprint)
                    ):
                        return self._failed(
                            step_name,
                            "substituição recusada: backup imutável verificado não encontrado",
                        )
                    self._filesystem.remove_tree(path)
                elapsed = self._files.copy_from(
                    installation.source_server,
                    Path(installation.source_path),
                    path.parent,
                    planned_step.excludes,
                    run_id,
                )
            except (InfrastructureError, OSError) as exc:
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

        if step_name == "preflight":
            return self._ok(step_name, False, "ponto de controle de diagnóstico concluído")
        if step_name == "snapshot":
            return self._snapshot_widgets(step_name, installation, path, context, run_id)
        if step_name == "widget_validation":
            return self._validate_widgets(step_name, installation, path, context, run_id)
        if step_name == "pending_search_replace":
            return self._search_replace(step_name, path, context, run_id, dry_run=False)
        if step_name == "managed_plugin_refresh":
            return self._refresh_managed_plugins(
                step_name, planned_step.installation_id, installation, path, context, run_id
            )
        command = self._wp_commands.get(step_name)
        if command:
            if step_name == "third_party_plugin_update":
                managed = getattr(context.get("manifest"), "managed_plugins", ())
                if managed:
                    command = (*command, f"--exclude={','.join(item.slug for item in managed)}")
            output = self._wordpress.update(path, command, run_id)
            return StepResult(step_name, StepStatus.SUCCEEDED, True, output)
        raise UnsafeOperationError(f"Etapa mutável desconhecida: {step_name}")

    def validate(self, step_name: str, context: Dict[str, Any]) -> StepResult:
        """Execute only native dry-runs whose safety was explicitly reviewed here."""
        planned_step = context.get("planned_step")
        if not isinstance(planned_step, PlannedStep):
            raise UnsafeOperationError(f"Etapa {step_name} não possui plano de validação")
        if planned_step.capability is not StepCapability.MUTABLE_WITH_NATIVE_DRY_RUN:
            raise UnsafeOperationError(f"Etapa {step_name} não possui dry-run nativo autorizado")
        installation = context.get("installations", {}).get(
            planned_step.installation_id, context["installation"]
        )
        if installation.destination_environment is not Environment.TEST:
            raise UnsafeOperationError("Validações operacionais são proibidas fora de TESTE")
        if step_name != "pending_search_replace":
            raise UnsafeOperationError(f"Dry-run nativo desconhecido: {step_name}")
        return self._search_replace(
            step_name,
            Path(installation.destination_path),
            context,
            str(context["run_id"]),
            dry_run=True,
        )

    def _snapshot_source_database(
        self,
        step_name: str,
        installation_id: str,
        installation: Any,
        run_id: str,
        recovery_data: Dict[str, Dict[str, str]],
    ) -> StepResult:
        if self._source_wordpress is None:
            return self._failed(
                step_name, "o adapter de inspeção WordPress remota não está configurado"
            )
        server = self._source_wordpress.get_server(installation.source_server)
        if server.environment is not installation.source_environment:
            raise UnsafeOperationError("O ambiente do servidor não coincide com o da origem")
        try:
            source_path = Path(installation.source_path)
            source_name = self._source_wordpress.get_config(
                installation.source_server, source_path, "DB_NAME", run_id
            )
            source_url = self._source_wordpress.get_site_url(
                installation.source_server, source_path, run_id
            )
            test_url = self._test_url_policy.resolve(
                source_url,
                str(getattr(installation, "test_url", None))
                if getattr(installation, "test_url", None)
                else None,
            )
            configured_source = getattr(installation, "source_database_endpoint", None)
            if configured_source:
                candidate_ids: tuple[str, ...] = (str(configured_source),)
            else:
                source_host = self._source_wordpress.get_config(
                    installation.source_server, source_path, "DB_HOST", run_id
                )
                host, port = self._parse_database_host(source_host)
                candidate_ids = tuple(
                    endpoint_id
                    for endpoint_id in self._databases.endpoint_ids()
                    if self._databases.get_database(endpoint_id).environment
                    is installation.source_environment
                    and self._databases.get_database(endpoint_id).host == host
                    and self._databases.get_database(endpoint_id).port == port
                )
            source_endpoints = tuple(
                endpoint_id
                for endpoint_id in candidate_ids
                if self._databases.get_database(endpoint_id).environment
                is installation.source_environment
                and source_name in self._databases.list_schemas(endpoint_id)
            )
        except (
            ConfigurationError,
            InfrastructureError,
            UnsafeOperationError,
            WordPressUnavailableError,
        ) as exc:
            return self._failed(step_name, str(exc))
        if not source_endpoints:
            return self._failed(
                step_name,
                "o banco MySQL de origem não existe no endpoint configurado ou descoberto",
            )
        if len(source_endpoints) > 1:
            return self._failed(step_name, "a origem MySQL foi identificada em mais de um endpoint")
        target_endpoints = [
            endpoint_id
            for endpoint_id in installation.allowed_database_endpoints
            if self._databases.get_database(endpoint_id).environment is Environment.TEST
        ]
        override = installation.database_override or self._database_overrides.get(installation_id)
        try:
            target = DatabaseLocator(
                self._databases, ProductionTestDatabaseNamingStrategy()
            ).locate(
                source_name,
                installation.database_aliases,
                target_endpoints,
                override=override,
            )
        except (AmbiguousDatabaseError, DatabaseNotFoundError) as exc:
            return self._failed(step_name, str(exc))
        self._database_runs[(run_id, installation_id)] = {
            "source_database_endpoint": source_endpoints[0],
            "source_database": source_name,
            "target_database_endpoint": target.endpoint_id,
            "target_database": target.database_name,
            "source_server": installation.source_server,
            "source_path": str(installation.source_path),
            "source_environment": installation.source_environment.value,
            "source_url": source_url,
            "test_url": test_url,
        }
        recovery_data.setdefault(installation_id, {}).update(
            self._database_runs[(run_id, installation_id)]
        )
        return self._ok(step_name, False, "origem e destino MySQL resolvidos sem ambiguidade")

    def _refresh_managed_plugins(
        self,
        step_name: str,
        installation_id: str,
        installation: Any,
        path: Path,
        context: Dict[str, Any],
        run_id: str,
    ) -> StepResult:
        manifest = context.get("manifest")
        plugins = getattr(manifest, "managed_plugins", ())
        if manifest is None:
            return self._failed(step_name, "manifesto ausente para registrar plugins gerenciados")
        if not plugins:
            manifest.managed_plugin_results = []
            return self._ok(step_name, False, "nenhum plugin gerenciado configurado")
        if self._managed_plugins is None:
            return self._failed(step_name, "adapter local de plugins gerenciados não configurado")

        parsed = self._parser.parse(
            str(path), installation_id, installation.destination_environment
        )
        self._parser.assert_safe_destructive_target(path, parsed)
        try:
            results = self._managed_plugins.refresh(parsed, plugins, run_id)
        except (InfrastructureError, UnsafeOperationError) as exc:
            return self._failed(step_name, str(exc))
        manifest.managed_plugin_results = list(results)
        failed = next(
            (item for item in results if item.status is ManagedPluginStatus.FAILED_PRESERVED),
            None,
        )
        if failed is not None:
            return self._failed(step_name, f"{failed.slug}: {failed.message}")
        refreshed = sum(1 for item in results if item.changed)
        skipped = sum(1 for item in results if item.status is ManagedPluginStatus.SKIPPED)
        return StepResult(
            step_name,
            StepStatus.SUCCEEDED,
            refreshed > 0,
            f"plugins gerenciados: {refreshed} substituídos, {skipped} skips explícitos",
            {"refreshed": float(refreshed), "skipped": float(skipped)},
        )

    def _snapshot_widgets(
        self,
        step_name: str,
        installation: Any,
        path: Path,
        context: Dict[str, Any],
        run_id: str,
    ) -> StepResult:
        manifest = context.get("manifest")
        if manifest is None:
            return self._failed(step_name, "manifesto ausente para persistir o snapshot de widgets")
        try:
            endpoint_id, database = self._locate_test_database(installation, path, run_id)
            snapshot = self._databases.snapshot_widgets(endpoint_id, database)
        except (DatabaseNotFoundError, AmbiguousDatabaseError, InfrastructureError) as exc:
            return self._failed(step_name, str(exc))
        except WordPressUnavailableError:
            return self._failed(step_name, "não foi possível descobrir o banco de TESTE")
        manifest.widget_snapshot = snapshot
        manifest.widget_diff = []
        return StepResult(
            step_name,
            StepStatus.SUCCEEDED,
            False,
            f"snapshot de referência persistido: {len(snapshot.options)} opções protegidas",
            {"protected_options": float(len(snapshot.options))},
        )

    def _validate_widgets(
        self,
        step_name: str,
        installation: Any,
        path: Path,
        context: Dict[str, Any],
        run_id: str,
    ) -> StepResult:
        manifest = context.get("manifest")
        reference = getattr(manifest, "widget_snapshot", None)
        if manifest is None or reference is None:
            return self._failed(step_name, "snapshot de referência de widgets ausente")
        try:
            endpoint_id, database = self._locate_test_database(installation, path, run_id)
            current = self._databases.snapshot_widgets(endpoint_id, database)
        except (DatabaseNotFoundError, AmbiguousDatabaseError, InfrastructureError) as exc:
            return self._failed(step_name, str(exc))
        except WordPressUnavailableError:
            return self._failed(step_name, "não foi possível descobrir o banco de TESTE")

        events = compare_widgets(reference, current)
        manifest.widget_diff = self._widget_diff(events)
        if not events:
            return StepResult(
                step_name,
                StepStatus.SUCCEEDED,
                False,
                "widgets, sidebars e tema permanecem iguais ao snapshot de referência",
                {"widget_differences": 0.0},
            )
        if not context.get("restore_widgets", False):
            return StepResult(
                step_name,
                StepStatus.FAILED,
                False,
                f"validação detectou {len(events)} divergências; restauração não solicitada",
                {"widget_differences": float(len(events))},
            )

        try:
            self._databases.restore_widgets(endpoint_id, database, reference, run_id)
            restored = self._databases.snapshot_widgets(endpoint_id, database)
        except (DatabaseNotFoundError, InfrastructureError, UnsafeOperationError):
            return self._failed(step_name, "restauração falhou; a cópia de TESTE foi preservada")
        remaining = compare_widgets(reference, restored)
        manifest.widget_diff = self._widget_diff(remaining)
        if remaining:
            return StepResult(
                step_name,
                StepStatus.FAILED,
                False,
                f"restauração incompleta: permanecem {len(remaining)} divergências",
                {
                    "widget_differences": float(len(remaining)),
                    "detected_widget_differences": float(len(events)),
                },
            )
        return StepResult(
            step_name,
            StepStatus.SUCCEEDED,
            True,
            f"snapshot restaurado explicitamente após {len(events)} divergências",
            {
                "widget_differences": 0.0,
                "detected_widget_differences": float(len(events)),
                "restored_options": float(len(reference.options)),
            },
        )

    @staticmethod
    def _widget_diff(events: tuple[WidgetEvent, ...]) -> list[Dict[str, str]]:
        return [
            {
                "event_type": event.event_type.value,
                "table": event.table,
                "option_name": event.option_name,
            }
            for event in events
        ]

    def _locate_test_database(self, installation: Any, path: Path, run_id: str) -> tuple[str, str]:
        database = self._wordpress.get_config(path, "DB_NAME", run_id)
        matches = [
            endpoint_id
            for endpoint_id in installation.allowed_database_endpoints
            if self._databases.get_database(endpoint_id).environment is Environment.TEST
            and database in self._databases.list_schemas(endpoint_id)
        ]
        if not matches:
            raise DatabaseNotFoundError(
                "o banco configurado no WordPress não existe nos endpoints de TESTE autorizados"
            )
        if len(matches) > 1:
            raise AmbiguousDatabaseError(
                "AMBIGUOUS_DATABASE: o banco configurado existe em mais de um endpoint de TESTE"
            )
        return matches[0], database

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
                state.get("source_database_endpoint", state.get("source_endpoint", "")),
                state["source_database"],
                dump_path,
                run_id,
            )
            self._databases.import_dump(
                state.get("target_database_endpoint", state.get("target_endpoint", "")),
                state["target_database"],
                dump_path,
                run_id,
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
            state.get("target_database_endpoint", state.get("target_endpoint", "")),
            state["target_database"],
        )
        self._wordpress.set_config(path, values, run_id)
        self._database_runs.pop(key, None)
        return self._ok(step_name, True, "wp-config aponta para o banco do ambiente de teste")

    @staticmethod
    def _backup_path(app_root: Path, run_id: str, installation_id: str) -> Path:
        def component(value: str) -> str:
            safe = "".join(
                character if character.isalnum() or character in "._-" else "-"
                for character in value
            )
            safe = safe.strip(".-")
            if safe == value and len(safe) <= 48:
                return safe
            digest = sha256(value.encode("utf-8")).hexdigest()[:12]
            return f"{safe[:35]}-{digest}" if safe else digest

        return app_root / ".wp-modernizer-backups" / component(run_id) / component(installation_id)

    @staticmethod
    def _parse_database_host(value: str) -> tuple[str, int]:
        raw = value.strip()
        if not raw or any(character in raw for character in "\r\n\x00"):
            raise ConfigurationError("DB_HOST remoto está vazio ou contém caracteres inválidos")
        if raw.startswith("["):
            closing = raw.find("]")
            if closing < 1:
                raise ConfigurationError("DB_HOST remoto possui formato não suportado")
            host = raw[1:closing]
            suffix = raw[closing + 1 :]
            if not suffix:
                return host, 3306
            if not suffix.startswith(":") or not suffix[1:].isdigit():
                raise ConfigurationError("DB_HOST remoto possui socket ou formato não suportado")
            return host, int(suffix[1:])
        if raw.count(":") == 1:
            host, port = raw.rsplit(":", 1)
            if not host or not port.isdigit():
                raise ConfigurationError("DB_HOST remoto possui formato não suportado")
            return host, int(port)
        if ":" in raw:
            raise ConfigurationError(
                "DB_HOST remoto ambíguo; configure source_database_endpoint explicitamente"
            )
        return raw, 3306

    def _search_replace(
        self,
        step_name: str,
        path: Path,
        context: Dict[str, Any],
        run_id: str,
        *,
        dry_run: bool,
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
            if dry_run:
                return StepResult(
                    step_name,
                    StepStatus.VALIDATED,
                    False,
                    "nenhum search-replace pendente",
                )
            return self._ok(step_name, False, "nenhum search-replace pendente")
        explicit_url = pending.parameters.get("test_url") or None
        try:
            planned_step = context.get("planned_step")
            installation_id = getattr(planned_step, "installation_id", "")
            resolution = context.get("recovery_data", {}).get(installation_id, {})
            if dry_run and resolution.get("source_url") and resolution.get("test_url"):
                old_url = resolution["source_url"]
                new_url = resolution["test_url"]
            else:
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
                path, old_url, new_url, dry_run=dry_run, multisite=multisite, run_id=run_id
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
        if manifest is not None and not dry_run:
            for index, operation in enumerate(manifest.pending_operations):
                if (
                    operation.operation_type is pending.operation_type
                    and operation.parameters == pending.parameters
                    and not operation.completed
                ):
                    manifest.pending_operations[index] = replace(operation, completed=True)
                    break
        if dry_run:
            return StepResult(
                step_name,
                StepStatus.VALIDATED,
                False,
                f"search-replace validado pelo dry-run nativo: {changed_count} substituições",
                {"potential_replacements": float(changed_count)},
            )
        return StepResult(
            step_name,
            StepStatus.EXECUTED,
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
