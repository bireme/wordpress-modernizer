from __future__ import annotations

import json
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Any

import click

from wp_modernizer.application.ports import CommandRunner, SecretProvider
from wp_modernizer.application.service import ModernizerService
from wp_modernizer.config.loader import load_config
from wp_modernizer.config.models import ApplicationConfig
from wp_modernizer.diagnostics.capability import CapabilityProbe
from wp_modernizer.domain.enums import Operation, RunStatus
from wp_modernizer.domain.errors import ModernizerError
from wp_modernizer.domain.path_parser import InstallationPathParser
from wp_modernizer.infrastructure.command import SubprocessCommandRunner
from wp_modernizer.infrastructure.filesystem import LocalFileSystem
from wp_modernizer.infrastructure.mysql.adapter import MySQLAdapter
from wp_modernizer.infrastructure.runtime_operations import RuntimeOperations
from wp_modernizer.infrastructure.secrets import EnvironmentSecretProvider
from wp_modernizer.infrastructure.ssh.adapter import RSyncSSHAdapter
from wp_modernizer.infrastructure.state import JsonStateStore
from wp_modernizer.infrastructure.time import SystemClock, UUIDGenerator
from wp_modernizer.infrastructure.wpcli.adapter import WPCLIAdapter


def build_service(
    config: ApplicationConfig,
    *,
    runner: CommandRunner | None = None,
    secrets: SecretProvider | None = None,
) -> ModernizerService:
    """Composition root da aplicação; dependências opcionais mantêm os testes sem subprocessos."""
    command_runner = runner or SubprocessCommandRunner()
    secret_provider = secrets or EnvironmentSecretProvider()
    filesystem = LocalFileSystem()
    ssh = RSyncSSHAdapter(config.servers, secret_provider, command_runner)
    mysql = MySQLAdapter(config.databases, secret_provider, command_runner)
    wpcli = WPCLIAdapter(command_runner)
    operations = RuntimeOperations(
        ssh,
        mysql,
        wpcli,
        InstallationPathParser(config.allowed_app_roots),
        database_overrides=config.database_overrides,
    )
    return ModernizerService(
        config,
        CapabilityProbe(command_runner, filesystem),
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
    help="Garante que etapas mutáveis sejam planejadas, mas não executadas.",
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
    if hasattr(value, "value"):
        return value.value
    if hasattr(value, "__dataclass_fields__"):
        return _serialize(asdict(value))
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {key: _serialize(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
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
cli.add_command(_read_command(Operation.DIAGNOSE))


def _mutable_command(operation: Operation) -> Any:
    @click.command(name=operation.value)
    @click.argument("installation_id")
    @click.option(
        "--dry-run",
        "command_dry_run",
        is_flag=True,
        help="Planeja sem alterar estado (também disponível globalmente).",
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
        try:
            report = context.service.execute(
                operation,
                installation_id,
                dry_run=context.dry_run or command_dry_run,
                replace_existing=replace_existing,
                restore_widgets=restore_widgets,
            )
            _emit(report, as_json)
            if report.status is RunStatus.UPDATE_FAILED_PRESERVED:
                raise click.exceptions.Exit(2)
        except ModernizerError as exc:
            raise click.ClickException(str(exc)) from exc

    return command


for _operation in (Operation.MIGRATE, Operation.UPDATE, Operation.PIPELINE):
    cli.add_command(_mutable_command(_operation))


@cli.command()
@click.argument("installation_id")
@click.option("--run-id", required=True)
@click.option("--dry-run", "command_dry_run", is_flag=True)
@click.option("--json", "as_json", is_flag=True)
@click.pass_obj
def resume(
    context: Context, installation_id: str, run_id: str, command_dry_run: bool, as_json: bool
) -> None:
    """Continua uma execução preservada após verificar o estado da intervenção manual."""
    try:
        _emit(
            context.service.resume(installation_id, run_id, context.dry_run or command_dry_run),
            as_json,
        )
    except ModernizerError as exc:
        raise click.ClickException(str(exc)) from exc


def main() -> int:
    try:
        cli()
    except click.ClickException as exc:
        exc.show(file=sys.stderr)
        return exc.exit_code
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
