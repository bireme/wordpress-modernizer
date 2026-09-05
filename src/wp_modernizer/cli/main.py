from __future__ import annotations

import json
import sys
from contextlib import nullcontext
from dataclasses import asdict
from pathlib import Path
from typing import Any, Callable

import click

from wp_modernizer.application.ports import CommandRunner, ExecutableLocator, SecretProvider
from wp_modernizer.application.service import ModernizerService
from wp_modernizer.config.loader import load_config
from wp_modernizer.config.models import ApplicationConfig
from wp_modernizer.diagnostics.capability import CapabilityProbe
from wp_modernizer.domain.enums import Environment, Operation, RunStatus
from wp_modernizer.domain.errors import ModernizerError
from wp_modernizer.domain.path_parser import InstallationPathParser
from wp_modernizer.infrastructure.command import SubprocessCommandRunner
from wp_modernizer.infrastructure.filesystem import LocalFileSystem
from wp_modernizer.infrastructure.managed_plugins import ManagedPluginRefresher
from wp_modernizer.infrastructure.mysql.adapter import MySQLAdapter
from wp_modernizer.infrastructure.runtime_operations import RuntimeOperations
from wp_modernizer.infrastructure.secrets import EnvironmentSecretProvider
from wp_modernizer.infrastructure.ssh import (
    FileTransferRouter,
    PasswordSFTPAdapter,
    RSyncSSHAdapter,
)
from wp_modernizer.infrastructure.state import JsonStateStore
from wp_modernizer.infrastructure.time import SystemClock, UUIDGenerator
from wp_modernizer.infrastructure.wp_config_writer import WordPressConfigWriter
from wp_modernizer.infrastructure.wpcli.adapter import WPCLIAdapter
from wp_modernizer.observability.logging import (
    ObservedCommandRunner,
    StructuredProgressReporter,
    active_execution_logger,
    create_execution_log,
)
from wp_modernizer.pipeline.progress import NullProgressReporter

from .output import (
    CompositeProgressReporter,
    TerminalProgressReporter,
    emit_diagnosis,
    emit_run_summary,
)


def build_service(
    config: ApplicationConfig,
    *,
    runner: CommandRunner | None = None,
    secrets: SecretProvider | None = None,
    ssh_client_factory: Callable[[], Any] | None = None,
    executable_locator: ExecutableLocator | None = None,
) -> ModernizerService:
    """Composition root da aplicação; dependências opcionais mantêm os testes sem subprocessos."""
    command_runner = ObservedCommandRunner(runner or SubprocessCommandRunner())
    secret_provider = secrets or EnvironmentSecretProvider()
    filesystem = LocalFileSystem()
    key_transport = RSyncSSHAdapter(config.servers, secret_provider, command_runner)
    password_transport = (
        PasswordSFTPAdapter(config.servers, secret_provider, client_factory=ssh_client_factory)
        if ssh_client_factory is not None
        else PasswordSFTPAdapter(config.servers, secret_provider)
    )
    ssh = FileTransferRouter(config.servers, key_transport, password_transport)
    mysql = MySQLAdapter(config.databases, secret_provider, command_runner)
    wpcli = WPCLIAdapter(command_runner)
    config_writer = WordPressConfigWriter()
    database_endpoints = {
        installation.effective_destination_path: tuple(
            endpoint_id
            for endpoint_id in installation.allowed_database_endpoints
            if config.databases[endpoint_id].environment is Environment.TEST
        )
        for installation in config.installations.values()
    }
    operations = RuntimeOperations(
        ssh,
        mysql,
        wpcli,
        InstallationPathParser(config.allowed_app_roots),
        database_overrides=config.database_overrides,
        managed_plugins=ManagedPluginRefresher(filesystem, command_runner),
        source_inspection=ssh,
        filesystem=filesystem,
        config_writer=config_writer,
    )
    return ModernizerService(
        config,
        CapabilityProbe(
            command_runner,
            filesystem,
            database=mysql,
            wordpress=wpcli,
            database_endpoints=database_endpoints,
            executable_locator=executable_locator,
        ),
        JsonStateStore(config.state_directory),
        filesystem,
        SystemClock(),
        UUIDGenerator(),
        operations,
    )


class Context:
    def __init__(self, config_path: Path, dry_run: bool) -> None:
        config = load_config(config_path)
        self.service = build_service(config)
        self.config = config
        self.dry_run = dry_run


@click.group(context_settings={"help_option_names": ["-h", "--help"]})
@click.option(
    "--config",
    "config_path",
    type=click.Path(path_type=Path),
    default=Path("config.yaml"),
    show_default=True,
)
@click.option(
    "--dry-run",
    is_flag=True,
    help="Não altera o alvo; valida somente leituras e dry-runs nativos autorizados.",
)
@click.version_option()
@click.pass_context
def cli(ctx: click.Context, config_path: Path, dry_run: bool) -> None:
    """Prepara e atualiza cópias de TESTE do WordPress. Nunca implanta em produção."""
    try:
        ctx.obj = Context(config_path, dry_run)
    except ModernizerError as exc:
        raise click.ClickException(str(exc)) from exc


def _emit(payload: Any, as_json: bool) -> None:
    serializable = _serialize(payload)
    if as_json:
        click.echo(json.dumps(serializable, indent=2, sort_keys=True))
    elif isinstance(serializable, dict):
        for key, value in serializable.items():
            click.echo(f"{key}: {value}")
    else:
        click.echo(str(serializable))


def _serialize(value: Any) -> Any:
    if isinstance(value, bytes):
        return {
            "encoding": "hex",
            "value": value.hex(),
        }
    if hasattr(value, "value"):
        return value.value
    if hasattr(value, "__dataclass_fields__"):
        return _serialize(asdict(value))
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {key: _serialize(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_serialize(item) for item in value]
    return value


def _read_command(operation: Operation) -> Any:
    @click.command(name=operation.value)
    @click.argument("installation_id")
    @click.option("--json", "as_json", is_flag=True, help="Emite JSON legível por máquina.")
    @click.pass_obj
    def command(context: Context, installation_id: str, as_json: bool) -> None:
        try:
            method = getattr(context.service, operation.value)
            _emit(method(installation_id), as_json)
        except ModernizerError as exc:
            raise click.ClickException(str(exc)) from exc

    return command


cli.add_command(_read_command(Operation.INVENTORY))
cli.add_command(_read_command(Operation.PLAN))


@cli.command()
@click.argument("installation_id")
@click.option("--json", "as_json", is_flag=True, help="Emite JSON legível por máquina.")
@click.pass_obj
def diagnose(context: Context, installation_id: str, as_json: bool) -> None:
    """Verifica as capacidades disponíveis para uma instalação."""
    execution_log = create_execution_log(
        context.config.state_directory, Operation.DIAGNOSE.value, installation_id
    )
    try:
        with active_execution_logger(execution_log.structured):
            execution_log.structured.event(
                "run_started", installation=installation_id, operation="diagnose"
            )
            report = context.service.diagnose(installation_id)
            for capability in report["capabilities"]:
                execution_log.structured.event("capability_result", **capability)
            execution_log.structured.event("run_finished", report=report)
        if as_json:
            _emit({**report, "log_path": str(execution_log.path)}, True)
        else:
            emit_diagnosis(report, str(execution_log.path))
    except ModernizerError as exc:
        execution_log.structured.event("run_failed", reason=str(exc))
        raise click.ClickException(str(exc)) from exc
    finally:
        execution_log.close()


def _mutable_command(operation: Operation) -> Any:
    @click.command(name=operation.value)
    @click.argument("installation_id")
    @click.option(
        "--dry-run",
        "command_dry_run",
        is_flag=True,
        help="Não altera o alvo e valida apenas operações seguras (também disponível globalmente).",
    )
    @click.option(
        "--replace-existing",
        is_flag=True,
        help="Cria uma cópia de segurança e substitui uma cópia de TESTE existente.",
    )
    @click.option(
        "--restore-widgets",
        is_flag=True,
        help="Restaura explicitamente o instantâneo de referência dos widgets.",
    )
    @click.option("--json", "as_json", is_flag=True)
    @click.pass_obj
    def command(
        context: Context,
        installation_id: str,
        command_dry_run: bool,
        replace_existing: bool,
        restore_widgets: bool,
        as_json: bool,
    ) -> None:
        execution_log = None
        try:
            try:
                execution_log = create_execution_log(
                    context.config.state_directory, operation.value, installation_id
                )
            except OSError:
                execution_log = None
            structured = (
                StructuredProgressReporter(execution_log.structured)
                if execution_log is not None
                else NullProgressReporter()
            )
            reporter = (
                structured
                if as_json
                else CompositeProgressReporter(
                    (TerminalProgressReporter(operation.value), structured)
                )
            )
            logging_context = (
                active_execution_logger(execution_log.structured)
                if execution_log is not None
                else nullcontext()
            )
            with logging_context:
                report = context.service.execute(
                    operation,
                    installation_id,
                    dry_run=context.dry_run or command_dry_run,
                    replace_existing=replace_existing,
                    restore_widgets=restore_widgets,
                    reporter=reporter,
                )
            if as_json:
                payload = _serialize(report)
                if execution_log is not None:
                    payload["log_path"] = str(execution_log.path)
                click.echo(json.dumps(payload, indent=2, sort_keys=True))
            else:
                log_path = str(execution_log.path) if execution_log else "unavailable"
                emit_run_summary(report, log_path, operation.value)
            if report.status is RunStatus.UPDATE_FAILED_PRESERVED:
                raise click.exceptions.Exit(2)
        except ModernizerError as exc:
            if execution_log is not None:
                execution_log.structured.event("run_failed", reason=str(exc))
            detail = str(exc)
            if not as_json and execution_log is not None:
                detail += f"\nSee complete log:\n  {execution_log.path}"
            raise click.ClickException(detail) from exc
        finally:
            if execution_log is not None:
                execution_log.close()

    return command


for _operation in (Operation.MIGRATE, Operation.UPDATE, Operation.PIPELINE):
    cli.add_command(_mutable_command(_operation))


@cli.command()
@click.argument("installation_id")
@click.option("--run-id", required=True)
@click.option("--dry-run", "command_dry_run", is_flag=True)
@click.option(
    "--restore-widgets",
    is_flag=True,
    help="Restaura explicitamente o snapshot de widgets durante este resume.",
)
@click.option("--json", "as_json", is_flag=True)
@click.pass_obj
def resume(
    context: Context,
    installation_id: str,
    run_id: str,
    command_dry_run: bool,
    restore_widgets: bool,
    as_json: bool,
) -> None:
    """Continua uma execução preservada após verificar o estado da intervenção manual."""
    execution_log = create_execution_log(
        context.config.state_directory, Operation.RESUME.value, installation_id, run_id=run_id
    )
    try:
        structured = StructuredProgressReporter(execution_log.structured)
        reporter = (
            structured
            if as_json
            else CompositeProgressReporter((TerminalProgressReporter("resume"), structured))
        )
        with active_execution_logger(execution_log.structured):
            report = context.service.resume(
                installation_id,
                run_id,
                context.dry_run or command_dry_run,
                restore_widgets=restore_widgets,
                reporter=reporter,
            )
        if as_json:
            payload = _serialize(report)
            payload["log_path"] = str(execution_log.path)
            click.echo(json.dumps(payload, indent=2, sort_keys=True))
        else:
            emit_run_summary(report, str(execution_log.path), "resume")
    except ModernizerError as exc:
        execution_log.structured.event("run_failed", reason=str(exc))
        detail = str(exc)
        if not as_json:
            detail += f"\nSee complete log:\n  {execution_log.path}"
        raise click.ClickException(detail) from exc
    finally:
        execution_log.close()


def main() -> int:
    try:
        cli()
    except click.ClickException as exc:
        exc.show(file=sys.stderr)
        return exc.exit_code
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
