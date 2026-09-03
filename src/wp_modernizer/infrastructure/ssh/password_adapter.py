from __future__ import annotations

import fnmatch
import os
import shlex
import socket
import stat
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


class _RejectUnknownHostKey:
    def missing_host_key(self, client: Any, hostname: str, key: Any) -> None:
        del client, hostname, key
        raise HostKeyVerificationError(
            "A chave do host SSH não consta nos arquivos known_hosts confiáveis"
        )


class PasswordSFTPAdapter:
    """Copia árvores por SFTP; a senha é entregue somente à API SSH em memória."""

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
                "O adaptador SFTP por senha aceita apenas servidores configurados com password"
            )
        remote_source = PurePosixPath(str(source))
        if not remote_source.is_absolute() or not remote_source.name:
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

    def get_config(self, server_id: str, path: Path, name: str, run_id: str) -> str:
        del run_id
        if name not in {"DB_NAME", "DB_HOST"}:
            raise ConfigurationError("A leitura remota solicitou uma constante não autorizada")
        return self._read_wordpress(server_id, path, ["config", "get", name])

    def get_site_url(self, server_id: str, path: Path, run_id: str) -> str:
        del run_id
        return self._read_wordpress(
            server_id,
            path,
            ["--skip-plugins", "--skip-themes", "option", "get", "siteurl"],
        )

    def _read_wordpress(self, server_id: str, path: Path, arguments: list[str]) -> str:
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
        username = self._secrets.get(server.username_secret)
        password = self._secrets.get(server.password_secret)
        client = self._client_factory()
        try:
            self._configure_host_verification(client, server)
            self._connect(client, server, username, password)
            command = shlex.join(["wp", f"--path={remote_path}", *arguments])
            _stdin, stdout, stderr = client.exec_command(command, timeout=60)
            if stdout.channel.recv_exit_status() != 0:
                # stderr may include secrets emitted by WordPress/PHP and is deliberately ignored.
                stderr.read()
                raise WordPressUnavailableError(
                    "não foi possível ler a configuração WordPress na origem remota"
                )
            raw = bytes(stdout.read())
        except WordPressUnavailableError:
            raise
        except (socket.timeout, TimeoutError) as exc:
            raise CommandTimeoutError("A leitura WordPress remota excedeu o limite de 60s") from exc
        except (OSError, paramiko.SSHException) as exc:
            raise WordPressUnavailableError(
                "não foi possível ler a configuração WordPress na origem remota"
            ) from exc
        finally:
            client.close()
        value = raw.decode("utf-8", errors="replace").strip()
        if not value:
            raise WordPressUnavailableError("a configuração WordPress remota está vazia")
        return value

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
        except paramiko.BadHostKeyException as exc:
            raise HostKeyVerificationError(
                "A chave do host SSH mudou ou não corresponde à identidade confiável"
            ) from exc
        except paramiko.BadAuthenticationType:
            raise AuthenticationRefusedError(
                "O servidor SSH recusou o método de autenticação por senha"
            ) from None
        except paramiko.AuthenticationException:
            # O protocolo normalmente não revela se o usuário ou a senha estava incorreto.
            raise PasswordAuthenticationError(
                "Autenticação SSH recusada; verifique o usuário e a senha configurados"
            ) from None
        except (socket.timeout, TimeoutError) as exc:
            raise CommandTimeoutError(
                f"A conexão SSH excedeu o limite de {self.CONNECT_TIMEOUT_SECONDS:g}s"
            ) from exc
        except (paramiko.SSHException, OSError) as exc:
            raise RemoteHostUnreachableError(
                "Não foi possível alcançar ou negociar uma sessão com o host SSH"
            ) from exc

    def _transfer(
        self,
        client: Any,
        remote_source: PurePosixPath,
        destination_parent: Path,
        excludes: tuple[str, ...],
        started: float,
    ) -> None:
        sftp = None
        try:
            sftp = client.open_sftp()
            sftp.get_channel().settimeout(self.TRANSFER_TIMEOUT_SECONDS)
            destination_parent.mkdir(parents=True, exist_ok=True)
            self._copy_entry(
                sftp,
                remote_source,
                destination_parent / remote_source.name,
                PurePosixPath("."),
                excludes,
                started,
            )
        except (socket.timeout, TimeoutError) as exc:
            raise CommandTimeoutError(
                f"A transferência SFTP excedeu o limite de {self.TRANSFER_TIMEOUT_SECONDS:g}s"
            ) from exc
        except CommandTimeoutError:
            raise
        except (OSError, paramiko.SSHException) as exc:
            raise TransferError(
                "A transferência SFTP falhou; consulte o diagnóstico seguro"
            ) from exc
        finally:
            if sftp is not None:
                sftp.close()

    def _copy_entry(
        self,
        sftp: Any,
        remote: PurePosixPath,
        local: Path,
        relative: PurePosixPath,
        excludes: tuple[str, ...],
        started: float,
    ) -> None:
        self._assert_within_timeout(started)
        if relative != PurePosixPath(".") and self._is_excluded(relative, excludes):
            return
        attributes = sftp.lstat(remote.as_posix())
        mode = attributes.st_mode
        if stat.S_ISDIR(mode):
            if local.exists() and (not local.is_dir() or local.is_symlink()):
                raise OSError("o destino local conflita com um diretório remoto")
            local.mkdir(parents=True, exist_ok=True)
            for child in sftp.listdir_attr(remote.as_posix()):
                self._validate_entry_name(child.filename)
                child_relative = (
                    PurePosixPath(child.filename)
                    if relative == PurePosixPath(".")
                    else relative / child.filename
                )
                self._copy_entry(
                    sftp,
                    remote / child.filename,
                    local / child.filename,
                    child_relative,
                    excludes,
                    started,
                )
            self._preserve_metadata(local, attributes)
            return
        if stat.S_ISREG(mode):
            if local.is_symlink():
                raise OSError("o destino local contém um link simbólico inseguro")
            if local.exists() and local.is_dir():
                raise OSError("o destino local conflita com um arquivo remoto")
            sftp.get(
                remote.as_posix(),
                str(local),
                callback=lambda transferred, total: self._transfer_progress(
                    transferred, total, started
                ),
            )
            self._preserve_metadata(local, attributes)
            return
        if stat.S_ISLNK(mode):
            target = sftp.readlink(remote.as_posix())
            if local.exists() or local.is_symlink():
                local.unlink()
            local.symlink_to(target)
            return
        raise OSError("a origem contém um tipo de arquivo SFTP não suportado")

    def _assert_within_timeout(self, started: float) -> None:
        if time.monotonic() - started > self.TRANSFER_TIMEOUT_SECONDS:
            raise CommandTimeoutError(
                f"A transferência SFTP excedeu o limite de {self.TRANSFER_TIMEOUT_SECONDS:g}s"
            )

    def _transfer_progress(self, transferred: int, total: int, started: float) -> None:
        del transferred, total
        self._assert_within_timeout(started)

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
            pattern = remote.as_posix().lstrip("./")
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

    @staticmethod
    def _validate_entry_name(name: str) -> None:
        if name in {"", ".", ".."} or "/" in name or "\x00" in name:
            raise OSError("a origem SFTP retornou um nome de arquivo inseguro")

    @staticmethod
    def _preserve_metadata(path: Path, attributes: Any) -> None:
        path.chmod(stat.S_IMODE(attributes.st_mode))
        path.touch(exist_ok=True)
        if attributes.st_atime is not None and attributes.st_mtime is not None:
            os.utime(path, (attributes.st_atime, attributes.st_mtime), follow_symlinks=False)
