from __future__ import annotations

import os
import re
import tempfile
from pathlib import Path
from typing import Dict, Iterable

from wp_modernizer.application.ports import CommandRunner, SecretProvider
from wp_modernizer.config.models import ServerConfig
from wp_modernizer.domain.errors import ConfigurationError, InfrastructureError


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
        if server.authentication == "password":
            raise ConfigurationError(
                "SSH com senha requer um adaptador separado, sem linha de comando e revisado; "
                "autenticação por chave é o padrão público"
            )
        # O usuário vem de SecretProvider e, por isso, não pode fazer parte de argv. Um arquivo
        # efêmero 0600 é entendido diretamente pelo ssh e removido mesmo quando o rsync falha.
        username = self._secrets.get(server.username_secret)
        if not re.fullmatch(r"[A-Za-z0-9._@+-]+", username):
            raise ConfigurationError(
                "O usuário SSH fornecido pelo segredo contém caracteres inválidos"
            )
        lines = [
            "Host wp-modernizer-source",
            f"  HostName {server.host}",
            f"  Port {server.port}",
            f"  User {username}",
            "  StrictHostKeyChecking "
            + ("yes" if server.host_key_policy == "strict" else "accept-new"),
        ]
        if server.private_key:
            lines.append(f"  IdentityFile {server.private_key}")
        config_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False) as handle:
                config_path = Path(handle.name)
                handle.write("\n".join(lines) + "\n")
            os.chmod(config_path, 0o600)
            argv = [
                "rsync",
                "-a",
                "--info=stats2",
                "--protect-args",
                "-e",
                f"ssh -F {config_path}",
            ]
            for excluded in excludes:
                argv.extend(["--exclude", str(excluded)])
            argv.extend([f"wp-modernizer-source:{source}", str(destination_parent)])
            result = self._runner.run(argv, timeout=1800, correlation_id=run_id)
        finally:
            if config_path is not None:
                config_path.unlink(missing_ok=True)
        if result.return_code != 0:
            raise InfrastructureError(
                f"Falha no rsync sobre SSH (código {result.return_code}); consulte o log redigido"
            )
        return int(result.elapsed_seconds)
