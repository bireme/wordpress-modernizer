from __future__ import annotations

from pathlib import Path
from typing import Sequence

from wp_modernizer.application.ports import CommandRunner, FileSystem
from wp_modernizer.domain.enums import Environment, ManagedPluginStatus
from wp_modernizer.domain.errors import InfrastructureError, UnsafeOperationError
from wp_modernizer.domain.models import (
    ManagedPlugin,
    ManagedPluginResult,
    WordPressInstallation,
)


class ManagedPluginRefresher:
    """Substitui checkouts locais de plugins sem operar fora de ``wp-content/plugins``."""

    def __init__(self, filesystem: FileSystem, runner: CommandRunner) -> None:
        self._filesystem = filesystem
        self._runner = runner

    def refresh(
        self,
        installation: WordPressInstallation,
        plugins: Sequence[ManagedPlugin],
        run_id: str,
    ) -> tuple[ManagedPluginResult, ...]:
        if installation.environment is not Environment.TEST:
            raise UnsafeOperationError("refresh de plugins é proibido fora de TESTE")
        installation_path = installation.path
        plugins_root = installation_path / "wp-content" / "plugins"
        self._validate_plugins_root(installation_path, plugins_root)
        if not self._filesystem.exists(plugins_root):
            raise UnsafeOperationError("o diretório autorizado de plugins não existe")

        results: list[ManagedPluginResult] = []
        for plugin in plugins:
            result = self._refresh_one(plugins_root, plugin, run_id)
            results.append(result)
            if result.status is ManagedPluginStatus.FAILED_PRESERVED:
                break
        return tuple(results)

    def _refresh_one(
        self, plugins_root: Path, plugin: ManagedPlugin, run_id: str
    ) -> ManagedPluginResult:
        if plugin.strategy != "replace_from_git":
            raise UnsafeOperationError(f"strategy não suportada para {plugin.slug}")
        target = plugins_root / plugin.slug
        self._validate_direct_child(target, plugins_root, plugin.slug)

        try:
            dirty_reason = self._dirty_reason(target, run_id)
        except (InfrastructureError, OSError):
            return self._result(
                plugin,
                ManagedPluginStatus.FAILED_PRESERVED,
                False,
                "abort: não foi possível inspecionar o plugin; conteúdo preservado",
            )
        if dirty_reason is not None:
            status = (
                ManagedPluginStatus.SKIPPED
                if plugin.dirty_policy == "skip"
                else ManagedPluginStatus.FAILED_PRESERVED
            )
            action = "skip explícito" if status is ManagedPluginStatus.SKIPPED else "abort"
            return self._result(
                plugin,
                status,
                False,
                f"{action}: plugin preservado porque {dirty_reason}",
            )

        staging = self._filesystem.create_temporary_directory(
            plugins_root, f".wp-modernizer-{plugin.slug}-"
        )
        self._validate_direct_child(staging, plugins_root)
        try:
            clone = self._runner.run(
                (
                    "git",
                    "clone",
                    "--quiet",
                    "--single-branch",
                    "--branch",
                    plugin.branch,
                    "--",
                    plugin.repository,
                    str(staging),
                ),
                timeout=300,
                correlation_id=run_id,
            )
            if clone.return_code != 0:
                return self._result(
                    plugin,
                    ManagedPluginStatus.FAILED_PRESERVED,
                    False,
                    "clone falhou; plugin existente preservado",
                )
            revision = self._revision(staging, run_id)
            self._replace_tree(plugins_root, target, staging, run_id)
            return self._result(
                plugin,
                ManagedPluginStatus.REFRESHED,
                True,
                "plugin substituído pelo checkout Git configurado",
                revision,
            )
        except (InfrastructureError, OSError):
            return self._result(
                plugin,
                ManagedPluginStatus.FAILED_PRESERVED,
                False,
                "falha local durante a substituição; plugin existente preservado",
            )
        finally:
            if self._filesystem.exists(staging):
                self._validate_direct_child(staging, plugins_root)
                self._filesystem.remove_tree(staging)

    def _dirty_reason(self, target: Path, run_id: str) -> str | None:
        if not self._filesystem.exists(target):
            return None
        status = self._runner.run(
            ("git", "status", "--porcelain", "--untracked-files=all"),
            cwd=target,
            correlation_id=run_id,
        )
        if status.return_code != 0:
            return "o diretório existente não é um checkout Git verificável"
        if status.stdout.strip():
            return "há modificações locais"
        return None

    def _revision(self, staging: Path, run_id: str) -> str:
        result = self._runner.run(("git", "rev-parse", "HEAD"), cwd=staging, correlation_id=run_id)
        if result.return_code != 0 or not result.stdout.strip():
            raise InfrastructureError("não foi possível determinar a revisão clonada")
        return result.stdout.strip().splitlines()[0]

    def _replace_tree(self, plugins_root: Path, target: Path, staging: Path, run_id: str) -> None:
        backup = plugins_root / f".wp-modernizer-{target.name}-{run_id}.backup"
        self._validate_direct_child(backup, plugins_root)
        if self._filesystem.exists(backup):
            raise UnsafeOperationError(
                "backup temporário de plugin já existe; substituição recusada"
            )
        had_target = self._filesystem.exists(target)
        if had_target:
            self._validate_direct_child(target, plugins_root, target.name)
            self._filesystem.move(target, backup)
        try:
            self._filesystem.move(staging, target)
        except Exception:
            if had_target and self._filesystem.exists(backup):
                self._filesystem.move(backup, target)
            raise
        if had_target:
            self._validate_direct_child(backup, plugins_root)
            try:
                self._filesystem.remove_tree(backup)
            except (InfrastructureError, OSError):
                # A limpeza faz parte da transação: restaura o checkout anterior e deixa
                # o clone novo no staging, que será removido pelo finally do chamador.
                self._filesystem.move(target, staging)
                self._filesystem.move(backup, target)
                raise

    def _validate_plugins_root(self, installation_path: Path, plugins_root: Path) -> None:
        expected = installation_path.resolve(strict=False) / "wp-content" / "plugins"
        if plugins_root.resolve(strict=False) != expected or self._filesystem.is_symlink(
            plugins_root
        ):
            raise UnsafeOperationError("diretório de plugins fora do destino local autorizado")

    def _validate_direct_child(
        self, path: Path, plugins_root: Path, expected_name: str | None = None
    ) -> None:
        if path.parent != plugins_root or path in {plugins_root, plugins_root.parent}:
            raise UnsafeOperationError("operação de plugin fora do diretório autorizado")
        if expected_name is not None and path.name != expected_name:
            raise UnsafeOperationError("destino do plugin não corresponde ao slug configurado")
        if self._filesystem.is_symlink(path):
            raise UnsafeOperationError("operação destrutiva recusada sobre link simbólico")

    @staticmethod
    def _result(
        plugin: ManagedPlugin,
        status: ManagedPluginStatus,
        changed: bool,
        message: str,
        revision: str | None = None,
    ) -> ManagedPluginResult:
        return ManagedPluginResult(
            plugin.slug,
            plugin.repository,
            plugin.branch,
            plugin.strategy,
            plugin.dirty_policy,
            status,
            changed,
            message,
            revision,
        )
