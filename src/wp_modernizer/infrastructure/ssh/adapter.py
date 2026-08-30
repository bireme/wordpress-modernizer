from __future__ import annotations

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

    def copy_from(
        self,
        server_id: str,
        source: Path,
        destination_parent: Path,
        excludes: Iterable[Path],
        run_id: str,
    ) -> int:
        server = self._servers[server_id]
        if server.authentication == "password":
            raise ConfigurationError(
                "SSH com senha requer um adaptador separado, sem linha de comando e revisado; "
                "autenticação por chave é o padrão público"
            )
        username = self._secrets.get(server.username_secret)
        ssh = [
            "ssh",
            "-p",
            str(server.port),
            "-o",
            "StrictHostKeyChecking="
            + ("yes" if server.host_key_policy == "strict" else "accept-new"),
        ]
        if server.private_key:
            ssh.extend(["-i", str(server.private_key)])
        argv = ["rsync", "-a", "--info=stats2", "--protect-args", "-e", " ".join(ssh)]
        for excluded in excludes:
            argv.extend(["--exclude", str(excluded)])
        argv.extend([f"{username}@{server.host}:{source}", str(destination_parent)])
        result = self._runner.run(argv, timeout=1800, correlation_id=run_id)
        if result.return_code != 0:
            raise InfrastructureError(f"Falha no rsync sobre SSH: {result.stderr}")
        return int(result.elapsed_seconds)
