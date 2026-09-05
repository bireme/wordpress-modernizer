from __future__ import annotations

import os
import re
import shlex
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Dict, Iterable, Iterator

from wp_modernizer.application.ports import CommandRunner, SecretProvider
from wp_modernizer.config.models import ServerConfig
from wp_modernizer.domain.errors import (
    ConfigurationError,
    InfrastructureError,
    WordPressUnavailableError,
)
from wp_modernizer.domain.models import SourceDatabaseConfiguration

from .source_config import parse_source_config


class RSyncSSHAdapter:
    def __init__(
        self, servers: Dict[str, ServerConfig], secrets: SecretProvider, runner: CommandRunner
    ) -> None:
        self._servers = servers
        self._secrets = secrets
        self._runner = runner

    def get_server(self, server_id: str) -> ServerConfig:
        try:
            return self._servers[server_id]
        except KeyError as exc:
            raise ConfigurationError(f"Servidor SSH desconhecido: {server_id}") from exc

    def copy_from(
        self,
        server_id: str,
        source: Path,
        destination_parent: Path,
        excludes: Iterable[Path],
        run_id: str,
    ) -> int:
        server = self.get_server(server_id)
        if server.authentication != "key":
            raise ConfigurationError(
                "O adaptador SSH/rsync aceita apenas servidores com autenticação por chave"
            )
        # O usuário vem de SecretProvider e, por isso, não pode fazer parte de argv. Um arquivo
        # efêmero 0600 é entendido diretamente pelo ssh e removido mesmo quando o rsync falha.
        username = self._secrets.get(server.username_secret)
        if not re.fullmatch(r"[A-Za-z0-9._@+-]+", username):
            raise ConfigurationError(
                "O usuário SSH fornecido pelo segredo contém caracteres inválidos"
            )
        with self._ssh_config(server, username) as config_path:
            argv = [
                "rsync",
                "-a",
                "--chmod=Du+w,Dg+w,Fu+w,Fg+w",
                "--info=stats2",
                "--protect-args",
                "-e",
                f"ssh -F {config_path}",
            ]
            for excluded in excludes:
                argv.extend(["--exclude", str(excluded)])
            argv.extend([f"wp-modernizer-source:{source}", str(destination_parent)])
            result = self._runner.run(argv, timeout=1800, correlation_id=run_id)
        if result.return_code != 0:
            raise InfrastructureError(
                f"Falha no rsync sobre SSH (código {result.return_code}); consulte o log redigido"
            )
        return int(result.elapsed_seconds)

    def inspect_config(
        self, server_id: str, path: Path, run_id: str
    ) -> SourceDatabaseConfiguration:
        config_path = self._wordpress_config_path(path)
        server = self.get_server(server_id)
        if server.authentication != "key":
            raise ConfigurationError(
                "O adaptador SSH por chave recebeu um servidor com autenticação incompatível"
            )
        username = self._secrets.get(server.username_secret)
        if not re.fullmatch(r"[A-Za-z0-9._@+-]+", username):
            raise ConfigurationError(
                "O usuário SSH fornecido pelo segredo contém caracteres inválidos"
            )
        remote_command = shlex.join(["cat", "--", str(config_path)])
        # Keep raw configuration out of CommandResult/redaction: redacting before parsing
        # would silently replace the production password. The private directory is 0700.
        with tempfile.TemporaryDirectory(prefix="wp-modernizer-source-") as directory:
            output = Path(directory) / "config"
            output.touch(mode=0o600)
            with self._ssh_config(server, username) as config_path:
                result = self._runner.run(
                    ["ssh", "-F", str(config_path), "wp-modernizer-source", "--", remote_command],
                    stdout_path=output,
                    timeout=60,
                    correlation_id=run_id,
                )
            if result.return_code != 0:
                raise WordPressUnavailableError(
                    "não foi possível ler wp-config.php na origem remota"
                )
            if not output.stat().st_size or output.stat().st_size > 1024 * 1024:
                raise WordPressUnavailableError(
                    "wp-config.php remoto está vazio ou excede o limite"
                )
            return parse_source_config(output.read_text(encoding="utf-8", errors="replace"))

    @contextmanager
    def _ssh_config(self, server: ServerConfig, username: str) -> Iterator[Path]:
        lines = [
            "Host wp-modernizer-source",
            f"  HostName {self._config_value(server.host)}",
            f"  Port {server.port}",
            f"  User {self._config_value(username)}",
            "  StrictHostKeyChecking "
            + ("yes" if server.host_key_policy == "strict" else "accept-new"),
        ]
        if server.private_key:
            lines.append(f"  IdentityFile {self._config_value(str(server.private_key))}")
        if server.known_hosts_file:
            lines.append(f"  UserKnownHostsFile {self._config_value(str(server.known_hosts_file))}")
        config_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False) as handle:
                config_path = Path(handle.name)
                handle.write("\n".join(lines) + "\n")
            os.chmod(config_path, 0o600)
            yield config_path
        finally:
            if config_path is not None:
                config_path.unlink(missing_ok=True)

    @staticmethod
    def _wordpress_config_path(path: Path) -> Path:
        if (
            not path.is_absolute()
            or ".." in path.parts
            or any(character in str(path) for character in "\r\n\x00")
        ):
            raise ConfigurationError("O caminho remoto WordPress deve ser absoluto e seguro")
        return path / "wp-config.php"

    @staticmethod
    def _config_value(value: str) -> str:
        if not value or any(character in value for character in "\r\n\x00"):
            raise ConfigurationError("A configuração SSH contém um valor vazio ou inválido")
        return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'
