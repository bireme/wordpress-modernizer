from __future__ import annotations

import fnmatch
import os
import shlex
import socket
import tarfile
import threading
import time
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Dict, Iterable, Sequence

import paramiko

from wp_modernizer.application.ports import SecretProvider
from wp_modernizer.config.models import ServerConfig
from wp_modernizer.domain.errors import (
    AuthenticationRefusedError,
    CommandTimeoutError,
    ConfigurationError,
    HostKeyVerificationError,
    PasswordAuthenticationError,
    RemoteHostUnreachableError,
    TransferError,
    WordPressUnavailableError,
)
from wp_modernizer.domain.models import SourceDatabaseConfiguration
from wp_modernizer.infrastructure.modernization import parse_wordpress_version

from .source_config import parse_source_config
from .tar_stream import ChannelStream, extract_tree


class _RejectUnknownHostKey:
    def missing_host_key(self, client: Any, hostname: str, key: Any) -> None:
        del client, hostname, key
        raise HostKeyVerificationError(
            "A chave do host SSH não consta nos arquivos known_hosts confiáveis"
        )


class PasswordSFTPAdapter:
    """Transfere tar por SSH com senha em memória; SFTP apenas para wp-config.php.

    O nome público é mantido por compatibilidade com a composição existente.
    """

    CONNECT_TIMEOUT_SECONDS = 30.0
    TRANSFER_TIMEOUT_SECONDS = 1800.0

    def __init__(
        self,
        servers: Dict[str, ServerConfig],
        secrets: SecretProvider,
        *,
        client_factory: Callable[[], Any] = paramiko.SSHClient,
    ) -> None:
        self._servers = servers
        self._secrets = secrets
        self._client_factory = client_factory

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
        del run_id  # correlação pertence ao chamador; credenciais nunca entram em relatórios
        server = self.get_server(server_id)
        if server.authentication != "password" or server.password_secret is None:
            raise ConfigurationError(
                "O adaptador SSH por senha aceita apenas servidores configurados com password"
            )
        remote_source = PurePosixPath(str(source))
        if (
            not remote_source.is_absolute()
            or not remote_source.name
            or ".." in remote_source.parts
            or "\x00" in str(remote_source)
        ):
            raise ConfigurationError("O caminho remoto de origem deve ser absoluto e nomeado")

        username = self._secrets.get(server.username_secret)
        password = self._secrets.get(server.password_secret)
        client = self._client_factory()
        started = time.monotonic()
        try:
            self._configure_host_verification(client, server)
            self._connect(client, server, username, password)
            self._transfer(
                client,
                remote_source,
                destination_parent,
                self._normalize_excludes(remote_source, excludes),
                started,
            )
        finally:
            client.close()
        return int(time.monotonic() - started)

    def inspect_config(
        self, server_id: str, path: Path, run_id: str
    ) -> SourceDatabaseConfiguration:
        return parse_source_config(
            self._read_wordpress_file(server_id, path, run_id, "wp-config.php")
        )

    def inspect_version(self, server_id: str, path: Path, run_id: str) -> str:
        return parse_wordpress_version(
            self._read_wordpress_file(server_id, path, run_id, "wp-includes/version.php")
        )

    def _read_wordpress_file(self, server_id: str, path: Path, run_id: str, filename: str) -> str:
        del run_id
        server = self.get_server(server_id)
        if server.authentication != "password" or server.password_secret is None:
            raise ConfigurationError(
                "O adaptador SSH por senha recebeu um servidor com autenticação incompatível"
            )
        remote_path = PurePosixPath(str(path))
        if (
            not remote_path.is_absolute()
            or ".." in remote_path.parts
            or any(character in str(remote_path) for character in "\r\n\x00")
        ):
            raise ConfigurationError("O caminho remoto WordPress deve ser absoluto e seguro")
        config_path = remote_path / filename
        username = self._secrets.get(server.username_secret)
        password = self._secrets.get(server.password_secret)
        client = self._client_factory()
        try:
            self._configure_host_verification(client, server)
            self._connect(client, server, username, password)
            sftp = client.open_sftp()
            try:
                sftp.get_channel().settimeout(60)
                with sftp.open(str(config_path), "rb") as remote_file:
                    raw = bytes(remote_file.read(1024 * 1024 + 1))
            finally:
                sftp.close()
        except WordPressUnavailableError:
            raise
        except (socket.timeout, TimeoutError) as exc:
            raise CommandTimeoutError("A leitura de wp-config.php excedeu o limite de 60s") from exc
        except (OSError, paramiko.SSHException) as exc:
            raise WordPressUnavailableError(
                "não foi possível ler o arquivo WordPress permitido na origem remota"
            ) from exc
        finally:
            client.close()
        if not raw or len(raw) > 1024 * 1024:
            raise WordPressUnavailableError(
                "o arquivo WordPress remoto está vazio ou excede o limite"
            )
        return raw.decode("utf-8", errors="replace")

    @staticmethod
    def _configure_host_verification(client: Any, server: ServerConfig) -> None:
        try:
            client.load_system_host_keys()
            if server.known_hosts_file is not None:
                client.load_host_keys(str(server.known_hosts_file))
        except OSError as exc:
            raise ConfigurationError(
                "Não foi possível carregar o arquivo known_hosts configurado"
            ) from exc
        if server.host_key_policy == "strict":
            client.set_missing_host_key_policy(_RejectUnknownHostKey())
        else:
            client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

    def _connect(self, client: Any, server: ServerConfig, username: str, password: str) -> None:
        try:
            client.connect(
                hostname=server.host,
                port=server.port,
                username=username,
                password=password,
                timeout=self.CONNECT_TIMEOUT_SECONDS,
                banner_timeout=self.CONNECT_TIMEOUT_SECONDS,
                auth_timeout=self.CONNECT_TIMEOUT_SECONDS,
                allow_agent=False,
                look_for_keys=False,
            )
        except HostKeyVerificationError:
            raise
        except paramiko.BadHostKeyException:
            raise HostKeyVerificationError(
                "A chave do host SSH mudou ou não corresponde à identidade confiável"
            ) from None
        except paramiko.BadAuthenticationType:
            raise AuthenticationRefusedError(
                "O servidor SSH recusou o método de autenticação por senha"
            ) from None
        except paramiko.AuthenticationException:
            # O protocolo normalmente não revela se o usuário ou a senha estava incorreto.
            raise PasswordAuthenticationError(
                "Autenticação SSH recusada; verifique o usuário e a senha configurados"
            ) from None
        except (socket.timeout, TimeoutError):
            raise CommandTimeoutError(
                f"A conexão SSH excedeu o limite de {self.CONNECT_TIMEOUT_SECONDS:g}s"
            ) from None
        except (paramiko.SSHException, OSError):
            raise RemoteHostUnreachableError(
                "Não foi possível alcançar ou negociar uma sessão com o host SSH"
            ) from None

    def _transfer(
        self,
        client: Any,
        remote_source: PurePosixPath,
        destination_parent: Path,
        excludes: tuple[str, ...],
        started: float,
    ) -> None:
        channel = None
        timer = None
        try:
            remaining = self.TRANSFER_TIMEOUT_SECONDS - (time.monotonic() - started)
            if remaining <= 0:
                raise TimeoutError
            transport = client.get_transport()
            if transport is None:
                raise TransferError("A sessão SSH não está disponível para transferência")
            channel = transport.open_session(timeout=remaining)
            remaining = self.TRANSFER_TIMEOUT_SECONDS - (time.monotonic() - started)
            if remaining <= 0:
                raise TimeoutError
            # Closing the channel also bounds exec_command's acknowledgement wait.
            timer = threading.Timer(remaining, channel.close)
            timer.daemon = True
            timer.start()
            stream = ChannelStream(channel, started + self.TRANSFER_TIMEOUT_SECONDS)
            command = (
                "LC_ALL=C tar --format=gnu --hard-dereference -cf - -C "
                + shlex.quote(str(remote_source.parent))
                + self._remote_exclude_options(remote_source.name, excludes)
                + " -- "
                + shlex.quote(remote_source.name)
            )
            channel.exec_command(os.fsencode(command))
            channel.shutdown_write()
            with tarfile.open(
                fileobj=stream, mode="r|", encoding="utf-8", errors="surrogateescape"
            ) as archive:
                extract_tree(
                    archive,
                    destination_parent,
                    remote_source.name,
                    lambda relative: any(
                        self._is_excluded(PurePosixPath(*relative.parts[:index]), excludes)
                        for index in range(1, len(relative.parts) + 1)
                    ),
                    stream.check_timeout,
                )
            # Drain padding/stdout AND stderr before waiting for the exit status.
            while stream.read(65536):
                pass
            status = stream.exit_status()
            if status != 0:
                raise TransferError(
                    f"A transferência SSH/tar falhou (código remoto {status}); "
                    "verifique tar e as permissões de leitura na origem"
                )
        except (
            OSError,
            EOFError,
            ValueError,
            tarfile.TarError,
            paramiko.SSHException,
            TransferError,
        ) as exc:
            if isinstance(exc, TimeoutError) or (
                time.monotonic() - started >= self.TRANSFER_TIMEOUT_SECONDS
            ):
                raise CommandTimeoutError(
                    "A transferência SSH/tar excedeu o limite configurado"
                ) from None
            if isinstance(exc, TransferError):
                raise
            # Never propagate remote stderr or third-party exception text (may contain secrets).
            raise TransferError(
                "A transferência SSH/tar falhou: stream inválido, conexão interrompida "
                "ou destino inacessível"
            ) from None
        finally:
            if timer is not None:
                timer.cancel()
            if channel is not None:
                channel.close()

    @staticmethod
    def _remote_exclude_options(root: str, excludes: tuple[str, ...]) -> str:
        """Translate literals and slash-free ASCII star globs; retain all local filters."""

        def literal(value: str) -> str:
            return "".join("\\" + char if char in "\\*?[]" else char for char in value)

        patterns = []
        for pattern in excludes:
            if "\x00" in pattern or pattern == ".":
                continue
            wildcard = any(char in pattern for char in "*?[")
            if wildcard and (
                "/" in pattern or not pattern.isascii() or any(char in pattern for char in "?[]\\")
            ):
                # Python matches Unicode characters; GNU tar under LC_ALL=C matches bytes.
                continue
            translated = pattern if wildcard else literal(pattern)
            prefix = literal(root) + "/"
            patterns.append(prefix + translated)
            if "/" not in pattern:
                patterns.append(prefix + "*/" + translated)
        if not patterns:
            return ""
        return " --wildcards --anchored --wildcards-match-slash" + "".join(
            " " + shlex.quote("--exclude=" + pattern) for pattern in patterns
        )

    @staticmethod
    def _normalize_excludes(source: PurePosixPath, excludes: Iterable[Path]) -> tuple[str, ...]:
        normalized = []
        for item in excludes:
            remote = PurePosixPath(str(item))
            if remote.is_absolute():
                try:
                    remote = remote.relative_to(source)
                except ValueError:
                    continue
            if ".." in remote.parts:
                continue
            pattern = remote.as_posix()
            if pattern and pattern != ".." and not pattern.startswith("../"):
                normalized.append(pattern.rstrip("/"))
        return tuple(normalized)

    @staticmethod
    def _is_excluded(relative: PurePosixPath, excludes: tuple[str, ...]) -> bool:
        value = relative.as_posix()
        for pattern in excludes:
            if not any(character in pattern for character in "*?["):
                if value == pattern or value.startswith(pattern + "/"):
                    return True
                if "/" not in pattern and relative.name == pattern:
                    return True
            elif fnmatch.fnmatchcase(value, pattern) or fnmatch.fnmatchcase(relative.name, pattern):
                return True
        return False
