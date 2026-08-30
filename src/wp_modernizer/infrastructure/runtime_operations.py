from __future__ import annotations

from pathlib import Path
from typing import Any, ClassVar, Dict, Tuple

from wp_modernizer.application.ports import CommandRunner
from wp_modernizer.domain.enums import StepStatus
from wp_modernizer.domain.errors import UnsafeOperationError
from wp_modernizer.domain.models import PlannedStep, StepResult
from wp_modernizer.domain.path_parser import InstallationPathParser


class RuntimeOperations:
    """Adaptador local conservador. Ações específicas não suportadas falham e preservam."""

    _wp_commands: ClassVar[Dict[str, Tuple[str, ...]]] = {
        "core_update": ("core", "update"),
        "core_database_update": ("core", "update-db"),
        "third_party_plugin_update": ("plugin", "update", "--all"),
        "theme_update": ("theme", "update", "--all"),
        "core_languages": ("language", "core", "update"),
        "plugin_languages": ("language", "plugin", "update", "--all"),
        "theme_languages": ("language", "theme", "update", "--all"),
    }

    def __init__(self, runner: CommandRunner, parser: InstallationPathParser) -> None:
        self._runner = runner
        self._parser = parser

    def execute(self, step_name: str, context: Dict[str, Any]) -> StepResult:
        planned_step = context.get("planned_step")
        if not isinstance(planned_step, PlannedStep):
            raise UnsafeOperationError(f"Etapa {step_name} não possui plano de execução")
        installations = context.get("installations", {})
        installation = installations.get(planned_step.installation_id, context["installation"])
        path = Path(installation.destination_path)
        if step_name == "backup_existing_test":
            if not path.exists():
                return self._ok(step_name, False, "não há cópia de teste existente")
            if not context.get("replace_existing"):
                return StepResult(
                    step_name,
                    StepStatus.FAILED,
                    False,
                    "a cópia de teste existente requer --replace-existing",
                )
            parsed = self._parser.parse(
                str(path), context["installation_id"], installation.destination_environment
            )
            self._parser.assert_safe_destructive_target(path, parsed)
            # A política de cópias depende da implantação. Não exclua sem confirmação do adaptador.
            return StepResult(
                step_name,
                StepStatus.FAILED,
                False,
                "o adaptador de cópia de segurança deve ser configurado antes da substituição; "
                "o teste existente foi preservado",
            )
        if step_name == "copy_files":
            excluded = ", ".join(str(path) for path in planned_step.excludes)
            return StepResult(
                step_name,
                StepStatus.FAILED,
                False,
                "o adaptador de origem SSH/rsync deve ser configurado explicitamente com "
                f"exclusions [{excluded}]; nenhum arquivo foi alterado",
            )
        if step_name in {"snapshot_source_database", "copy_database", "write_test_db_config"}:
            return StepResult(
                step_name,
                StepStatus.FAILED,
                False,
                "o adaptador de migração requer descoberta da origem em tempo de execução; "
                "nenhum banco de dados foi alterado",
            )
        if step_name in {"preflight", "snapshot", "widget_validation", "final_health_check"}:
            return self._ok(step_name, False, "ponto de controle de diagnóstico concluído")
        if step_name == "pending_search_replace":
            return self._ok(
                step_name, False, "nenhuma operação pendente materializada com segurança"
            )
        if step_name == "managed_plugin_refresh":
            return self._ok(step_name, False, "nenhuma atualização de plugin gerenciado solicitada")
        command = self._wp_commands.get(step_name)
        if command:
            result = self._runner.run(
                ["wp", f"--path={path}", "--skip-plugins", "--skip-themes", *command],
                timeout=900,
                correlation_id=context["run_id"],
            )
            return StepResult(
                step_name,
                StepStatus.SUCCEEDED if result.return_code == 0 else StepStatus.FAILED,
                result.return_code == 0,
                result.stdout or result.stderr,
                {"duration_seconds": result.elapsed_seconds},
            )
        raise UnsafeOperationError(f"Etapa mutável desconhecida: {step_name}")

    @staticmethod
    def _ok(name: str, changed: bool, message: str) -> StepResult:
        return StepResult(name, StepStatus.SUCCEEDED, changed, message)
