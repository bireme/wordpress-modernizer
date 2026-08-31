from __future__ import annotations

from pathlib import Path
from typing import Dict, Sequence

from wp_modernizer.application.ports import FileTransferPort
from wp_modernizer.config.models import ServerConfig
from wp_modernizer.domain.errors import ConfigurationError


class FileTransferRouter:
    """Seleciona explicitamente o transporte correspondente ao método de autenticação."""

    def __init__(
        self,
        servers: Dict[str, ServerConfig],
        key_transport: FileTransferPort,
        password_transport: FileTransferPort,
    ) -> None:
        self._servers = servers
        self._transports = {
            "key": key_transport,
            "password": password_transport,
        }

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
        excludes: Sequence[Path],
        run_id: str,
    ) -> int:
        server = self.get_server(server_id)
        return self._transports[server.authentication].copy_from(
            server_id, source, destination_parent, excludes, run_id
        )
